#!/usr/bin/env python3
"""Compare the 13 App screens with an unchanged, local design reference.

Screenshots establish rendering and navigation only. Synthetic observations do
not verify GPU, storage-service, physical connectivity or attribution accuracy.
Playwright and optional Pillow are needed only when the command runs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


APP_BASE = '/a/xlayer-telemetry-app'
SCREENS = {
    'overview': ('overview', 'Overview'),
    'analyze': ('analyze', 'Analyze'),
    'investigate': ('investigate', 'Investigate'),
    'timeline': ('timeline', 'Timeline'),
    'deep-dive': ('deep', 'Deep Dive'),
    'infrastructure': ('infra', 'Infrastructure'),
    'logs': ('logs', 'Logs & Events'),
    'start-here': ('start', 'Start Here'),
    'stage-correlation': ('stage', 'Stage Correlation'),
    'bottleneck-summary': ('summary', 'Bottleneck Summary'),
    'compute': ('compute', 'Compute & Communication'),
    'storage': ('storage', 'Data & Storage'),
    'signals': ('signals', 'Cross-Layer Signals'),
}
VIEWPORTS = {'desktop': {'width': 1440, 'height': 1000},
             'mobile': {'width': 390, 'height': 844}}
CONTEXT_KEYS = {'from', 'to', 'timezone'} | {
    'var-' + name for name in (
        'cluster', 'run_id', 'source_node', 'node', 'record_id', 'candidate_id',
        'phase_worker', 'matrix_gpu_entity', 'matrix_vllm_entity',
        'matrix_kv_entity', 'matrix_ray_entity', 'matrix_network_entity',
        'matrix_storage_entity', 'matrix_sandbox_entity', 'trace_id',
        'diagnosis_method', 'log_run_id', 'gpu', 'engine', 'sandbox_node',
        'phase', 'role', 'worker', 'device', 'mount', 'storage_system',
        'storage_node', 'storage_metric', 'detail_tab', 'training_max_age',
        'infra_component', 'workload')}


def owned_origin(url):
    """Never open an arbitrary remote endpoint or export embedded credentials."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in (
            '127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password:
        raise ValueError('Use an owned loopback Grafana URL without credentials')
    return urlunparse((parsed.scheme, parsed.netloc, '', '', '', ''))


def selected_screens(value):
    names = list(SCREENS) if not value else [part.strip() for part in value.split(',')]
    if not names or any(name not in SCREENS for name in names):
        raise ValueError('Unknown screen; choose from ' + ', '.join(SCREENS))
    return list(dict.fromkeys(names))


def selected_context(url, record=None):
    context = parse_qs(urlparse(url).query, keep_blank_values=True)
    if record is not None:
        context.update(record.get('selected_context') or record.get('context') or record)
    return {key: [str(value) for value in (values if isinstance(values, list) else [values])]
            for key, values in context.items() if key in CONTEXT_KEYS}


def app_url(origin, screen, context, theme):
    if screen not in SCREENS or theme not in ('light', 'dark'):
        raise ValueError('Unknown screen or theme')
    params = {key: values for key, values in context.items() if key in CONTEXT_KEYS}
    return owned_origin(origin) + APP_BASE + '/' + screen + '?' + urlencode(
        {**params, 'theme': theme}, doseq=True)


def normalized_context(context):
    """Scenes may rewrite numeric epochs as equivalent ISO timestamps."""
    result = {}
    for key, values in context.items():
        if key not in CONTEXT_KEYS or not values:
            continue
        normalized = []
        for value in values:
            value = str(value)
            if key in ('from', 'to') and re.match(r'^\d{4}-', value):
                try:
                    value = str(round(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000))
                except ValueError:
                    pass
            normalized.append('.*' if value == '$__all' else value)
        result[key] = sorted(normalized)
    return result


def context_changes(before, after):
    """Compare selected context; native Scenes may add default variables."""
    first, second = normalized_context(before), normalized_context(after)
    return {key: {'before': values, 'after': second.get(key)}
            for key, values in first.items() if second.get(key) != values}


def contact_sheet(output, records):
    """Create thumbnails for human review, not a misleading pixel-similarity score."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return {'available': False, 'reason': 'Optional Pillow is not installed'}
    files = []
    for viewport in VIEWPORTS:
        for theme in ('light', 'dark'):
            matching = [row for row in records if row['viewport'] == viewport
                        and row['theme'] == theme and row.get('reference_screenshot')
                        and (output / row['reference_screenshot']).is_file()
                        and (output / row['screenshot']).is_file()]
            if not matching:
                continue
            thumb_width = 360 if viewport == 'desktop' else 195
            thumb_height = round(thumb_width * VIEWPORTS[viewport]['height'] / VIEWPORTS[viewport]['width'])
            pair_width, row_height = thumb_width * 2 + 24, thumb_height + 45
            sheet = Image.new('RGB', (pair_width * 2, row_height * ((len(matching) + 1) // 2)), '#edf0f5')
            draw = ImageDraw.Draw(sheet)
            for index, row in enumerate(matching):
                x, y = (index % 2) * pair_width, (index // 2) * row_height
                draw.text((x + 6, y + 5), row['screen'] + ' | Reference / Grafana', fill='#1e293b')
                for offset, key in ((0, 'reference_screenshot'), (1, 'screenshot')):
                    with Image.open(output / row[key]) as source:
                        image = source.convert('RGB')
                        image.thumbnail((thumb_width, thumb_height))
                        sheet.paste(image, (x + 6 + offset * (thumb_width + 6), y + 26))
            name = f'comparison-{viewport}-{theme}.jpg'
            sheet.save(output / name, quality=88)
            files.append(name)
    return {'available': True, 'files': files,
            'interpretation': 'Visual comparison only; reference values and telemetry differ'}


def comparison_html(output, records):
    """Keep same-sized screenshots inspectable without an imaging dependency."""
    pairs = []
    for row in records:
        if not row.get('reference_screenshot'):
            continue
        images = ''.join('<figure><figcaption>' + caption + '</figcaption><img loading="lazy" src="'
            + escape(row[key], quote=True) + '" alt="' + escape(row['screen'] + ' ' + caption, quote=True)
            + '"></figure>' for key, caption in (('reference_screenshot', 'Design reference'),
                                                   ('screenshot', 'Actual synthetic Grafana')))
        pairs.append('<section><h2>' + escape(row['screen'] + ' · ' + row['viewport'] + ' · ' + row['theme'])
                     + '</h2><div class="pair">' + images + '</div></section>')
    name = 'comparison.html'
    (output / name).write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>XLayer design comparison</title>
<style>body{margin:24px;background:#eef1f5;color:#1e293b;font:14px system-ui}h1{font-size:22px}
h2{font-size:15px}section{border-top:1px solid #cdd6e1;padding:18px 0}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
figure{margin:0;min-width:0}figcaption{padding:6px}img{width:100%;height:auto;display:block;background:white}
@media(max-width:650px){body{margin:12px}.pair{grid-template-columns:1fr}}</style>
<h1>XLayer design comparison</h1><p>Same viewport dimensions. Reference numbers are fictional; Grafana data is synthetic.
Visual review does not establish metric correctness, resource ownership or causality. No pixel-equality score is claimed.</p>'''
                              + ''.join(pairs) + '</html>')
    return name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Owned loopback Grafana origin or selected App URL')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mockup', type=Path, help='Original standalone HTML; never modified')
    parser.add_argument('--context', type=Path, help='Existing selected_context/context JSON')
    parser.add_argument('--browser', help='Chromium executable used by existing browser validators')
    parser.add_argument('--iteration', type=int, required=True)
    parser.add_argument('--screens', help='Comma-separated route names; defaults to all 13')
    parser.add_argument('--report-only', action='store_true', help='Keep before-change captures even when checks fail')
    args = parser.parse_args()
    try:
        origin = owned_origin(args.url)
        screens = selected_screens(args.screens)
        if args.iteration < 1:
            raise ValueError('Iteration must be positive')
        if args.mockup and not args.mockup.is_file():
            raise ValueError('Design HTML must be an existing file')
        context = selected_context(args.url, json.loads(args.context.read_text()) if args.context else None)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if not context:
        context = {'from': ['now-5m'], 'to': ['now'], 'var-cluster': ['scenes-demo'],
                   'var-run_id': ['verl-agent-demo'], 'var-node': ['gpu-node-0'], 'var-gpu': ['0']}
    output = args.output / f'iteration-{args.iteration}'
    output.mkdir(parents=True, exist_ok=False)
    from playwright.sync_api import sync_playwright
    from browser_validate import EntryDiagnostics, diagnostic_text
    from ci_demo import native_dashboard_back

    records, failures, browser_errors = [], [], []
    manifest = {'iteration': args.iteration, 'generated_at': datetime.now(timezone.utc).isoformat(),
                'screens': screens, 'viewports': VIEWPORTS, 'themes': ['light', 'dark'],
                'data_origin': 'Synthetic demo, actual Grafana datasource requests',
                'reference_sha256': hashlib.sha256(args.mockup.read_bytes()).hexdigest() if args.mockup else None,
                'limitations': ['No physical GPU, RDMA, storage-cluster or multi-node validation',
                                'Different values/series prevent a meaningful pixel-equality score',
                                'Rendering, labels and HTTP status do not establish workload health or causality'],
                'captures': records, 'failures': failures}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'],
                    **({'executable_path': args.browser} if args.browser else {}))
        try:
            for viewport_name, viewport in VIEWPORTS.items():
                for theme in ('light', 'dark'):
                    page = browser.new_page(viewport=viewport, color_scheme=theme, locale='en-US', timezone_id='UTC')
                    errors = []
                    page.on('pageerror', lambda error, target=errors: target.append(diagnostic_text(error, 1000)) if len(target) < 20 else None)
                    diagnostics = EntryDiagnostics(page, output, viewport_name + '-' + theme, errors)
                    api_errors = []
                    def query_errors(response, target=api_errors):
                        if '/api/ds/query' not in urlparse(response.url).path or len(target) >= 32:
                            return
                        try:
                            payload = response.json()
                            results = payload.get('results', {}) if isinstance(payload, dict) else {}
                            if not isinstance(results, dict):
                                return
                            for ref, result in list(results.items())[:32]:
                                if isinstance(result, dict) and (result.get('error') or result.get('status', 200) >= 400):
                                    target.append({'ref_id': diagnostic_text(ref, 80),
                                                   'status': result.get('status'),
                                                   'error': diagnostic_text(result.get('error', ''), 1000)})
                                    if len(target) >= 32:
                                        break
                        except Exception:
                            # The existing HTTP metadata still records a response whose body is inaccessible.
                            return
                    page.on('response', query_errors)
                    reference = browser.new_page(viewport=viewport, color_scheme=theme) if args.mockup else None
                    if reference:
                        reference.goto(args.mockup.resolve().as_uri())
                        if theme == 'dark':
                            reference.locator('#theme-toggle').click()
                        assert reference.locator('body').get_attribute('data-theme') == theme
                    for screen in screens:
                        stem = f'{screen}-{viewport_name}-{theme}'
                        row = {'screen': screen, 'viewport': viewport_name, 'theme': theme,
                               'size': viewport, 'screenshot': stem + '.png'}
                        # Reuse the redacting observer with per-screen rather than entire-run bounds.
                        diagnostics.query_responses.clear()
                        diagnostics.http_failures.clear()
                        diagnostics.console_errors.clear()
                        errors.clear()
                        api_errors.clear()
                        if reference:
                            reference.goto(args.mockup.resolve().as_uri() + '#' + SCREENS[screen][0])
                            # A full document entry restores the reference's default theme.
                            if reference.locator('body').get_attribute('data-theme') != theme:
                                reference.locator('#theme-toggle').click()
                            reference.locator('#page').wait_for(state='visible')
                            row['reference_screenshot'] = 'reference-' + stem + '.png'
                            row['reference_overflow'] = reference.evaluate('document.documentElement.scrollWidth>innerWidth+2')
                            reference.screenshot(path=str(output / row['reference_screenshot']), full_page=False)
                        try:
                            target = app_url(origin, screen, context, theme)
                            # Actual App navigation when present; direct entry remains a tested compatibility path.
                            # Native titles retain their full canonical names; route identity is stable.
                            link = page.locator('.xlt a[href]')
                            clickable = next((element for element in link.all()
                                if urlparse(element.get_attribute('href') or '').path.rstrip('/') == APP_BASE + '/' + screen
                                and element.is_visible()), None)
                            menu = page.get_by_role('button', name='Open navigation', exact=True)
                            if not clickable and menu.count() and menu.is_visible():
                                menu.click()
                                clickable = next((element for element in link.all()
                                    if urlparse(element.get_attribute('href') or '').path.rstrip('/') == APP_BASE + '/' + screen
                                    and element.is_visible()), None)
                            if clickable:
                                clickable.click()
                                page.wait_for_url('**' + APP_BASE + '/' + screen + '?**', timeout=30000)
                                row['entry'] = 'App link'
                            else:
                                page.goto(target)
                                row['entry'] = 'Direct compatible route'
                            page.locator('.xlt').wait_for(state='visible', timeout=30000)
                            # A normal Grafana UI action leaves its global navigation
                            # available while matching the reference workspace width.
                            close_menu=page.get_by_role('button',name='Close menu',exact=True)
                            if close_menu.count() and close_menu.first.is_visible():
                                close_menu.first.click()
                            page.wait_for_timeout(1300)
                            actual_context = selected_context(page.url)
                            row['context_changes'] = context_changes(context, actual_context)
                            row['dom'] = page.evaluate("""() => {
                              const root=document.querySelector('.xlt'), text=root?.innerText||'';
                              return {document_overflow:document.documentElement.scrollWidth>innerWidth+2,
                                document_width:document.documentElement.scrollWidth,viewport_width:innerWidth,
                                titles:[...root.querySelectorAll('h1,h2,h3')].map(el=>el.innerText).slice(0,24),
                                tables:root.querySelectorAll('table').length,charts:root.querySelectorAll('canvas,svg').length,
                                quality_labels:['Missing','Stale','Unknown','Query failure','Shared','Sampled','Measured','Configured','Observed','Unavailable'].filter(label=>text.toLowerCase().includes(label.toLowerCase())),
                                empty_message: /No data|No completed|No .*available|unavailable/i.test(text),
                                theme:root.getAttribute('data-theme')||document.documentElement.getAttribute('data-theme'),
                                background:getComputedStyle(root).backgroundColor,
                                native_dashboard_links:[...root.querySelectorAll('a[href]')].filter(a=>a.getAttribute('href').startsWith('/d/')).length};
                            }""")
                            page.screenshot(path=str(output / row['screenshot']), full_page=False)
                            if row['dom']['theme'] != theme:
                                failures.append({'screen':stem,'check':'theme','actual':row['dom']['theme']})
                            if row['context_changes']:
                                failures.append({'screen': stem, 'check': 'context', 'changes': row['context_changes']})
                            if row['dom']['document_overflow']:
                                failures.append({'screen': stem, 'check': 'horizontal overflow', 'width': row['dom']['document_width']})
                        except Exception as error:
                            row['failure'] = diagnostic_text(error, 2000)
                            failures.append({'screen': stem, 'check': 'rendering/navigation', 'error': row['failure']})
                            diagnostics.label = stem
                            diagnostics.fail(error)
                            try:
                                page.screenshot(path=str(output / row['screenshot']), full_page=False, timeout=5000)
                            except Exception:
                                row['screenshot'] = stem + '-entry-failure.png'
                        row['browser_errors'] = list(errors)
                        row['console_errors'] = list(diagnostics.console_errors)
                        row['http_failures'] = list(diagnostics.http_failures)
                        row['datasource_responses'] = list(diagnostics.query_responses)
                        row['datasource_errors'] = list(api_errors)
                        if api_errors:
                            failures.append({'screen':stem,'check':'datasource error','errors':list(api_errors)})
                        records.append(row)
                        browser_errors.extend(errors)
                        if row['browser_errors']:
                            failures.append({'screen': stem, 'check': 'browser errors', 'errors': row['browser_errors']})
                    # Browser Back through the actual shell's route history must keep the selected investigation.
                    if len(screens) > 1:
                        previous = page.url
                        page.go_back(wait_until='domcontentloaded')
                        try:
                            if '/d/' in page.url:
                                native_dashboard_back(page, origin)
                            page.locator('.xlt').wait_for(timeout=15000)
                            # Scenes can write native default filters as additional URL entries.
                            back_entries = 1
                            while (urlparse(page.url).path == urlparse(previous).path
                                   and back_entries < 4):
                                page.go_back(wait_until='domcontentloaded')
                                page.locator('.xlt').wait_for(timeout=15000)
                                back_entries += 1
                            changes = context_changes(context, selected_context(page.url))
                            manifest.setdefault('back_checks', []).append({'viewport': viewport_name, 'theme': theme,
                                'route_changed': urlparse(page.url).path != urlparse(previous).path,
                                'history_entries': back_entries, 'context_changes': changes})
                            if urlparse(page.url).path == urlparse(previous).path:
                                failures.append({'screen': viewport_name + '-' + theme, 'check': 'Browser Back route'})
                            if changes:
                                failures.append({'screen': viewport_name + '-' + theme, 'check': 'Browser Back context', 'changes': changes})
                        except Exception as error:
                            failures.append({'screen': viewport_name + '-' + theme, 'check': 'Browser Back', 'error': diagnostic_text(error, 1000)})
                    if reference:
                        reference.close()
                    page.close()
        finally:
            browser.close()
            manifest['comparison_html'] = comparison_html(output, records)
            manifest['contact_sheets'] = contact_sheet(output, records)
            manifest['browser_errors'] = browser_errors[:80]
            manifest['complete'] = len(records) == len(screens) * len(VIEWPORTS) * 2 and not failures
            (output / 'validation.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'iteration': args.iteration, 'captures': len(records), 'failures': len(failures),
                      'manifest': str(output / 'validation.json')}))
    if failures and not args.report_only:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
