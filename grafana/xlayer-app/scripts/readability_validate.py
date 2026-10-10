#!/usr/bin/env python3
"""Inspect rendered text and navigation in an owned synthetic Grafana stack.

This is a bounded DOM contrast/readability check, not a WCAG certification.
Canvas text, complex paint effects and unverified SVG backgrounds need manual
review. Source availability and configured topology never establish node health.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse


VIEWPORTS = {'desktop': {'width': 1440, 'height': 1000},
             'tablet': {'width': 1024, 'height': 1000},
             'mobile': {'width': 390, 'height': 844}}


def parse_css_color(value):
    """Parse resolved sRGB CSS colors. Unsupported paint stays unknown."""
    if not isinstance(value, str) or len(value) > 160:
        return None
    value = value.strip().lower()
    value = {'black': '#000000', 'white': '#ffffff', 'transparent': '#00000000'}.get(value, value)
    if re.fullmatch(r'#[0-9a-f]{3,4}|#[0-9a-f]{6}|#[0-9a-f]{8}', value):
        digits = value[1:]
        if len(digits) in (3, 4):
            digits = ''.join(char * 2 for char in digits)
        if len(digits) == 6:
            digits += 'ff'
        return tuple(int(digits[index:index + 2], 16) / 255 for index in range(0, 8, 2))
    srgb = value.startswith('color(srgb ')
    match = re.fullmatch(r'(?:rgba?|color)\(([^)]+)\)', value)
    if not match:
        return None
    body = match.group(1)
    if srgb:
        body = body[len('srgb '):]
    elif value.startswith('color('):
        return None
    parts = re.split(r'\s*[,/]\s*|\s+', body.strip())
    if len(parts) not in (3, 4):
        return None
    try:
        channels = [float(part[:-1]) / 100 if part.endswith('%') else float(part) / (1 if srgb else 255)
                    for part in parts[:3]]
        alpha = float(parts[3][:-1]) / 100 if len(parts) == 4 and parts[3].endswith('%') else float(parts[3]) if len(parts) == 4 else 1
        values = (*channels, alpha)
        if not all(math.isfinite(channel) for channel in values):
            return None
        return tuple(max(0, min(1, channel)) for channel in values)
    except ValueError:
        return None


def composite(front, back):
    """Porter-Duff source-over for unpremultiplied RGBA, preserving opacity."""
    alpha = front[3] + back[3] * (1 - front[3])
    if alpha == 0:
        return (0, 0, 0, 0)
    return tuple((front[index] * front[3] + back[index] * back[3] * (1 - front[3])) / alpha
                 for index in range(3)) + (alpha,)


def effective_colors(foreground, layers, foreground_opacity=1):
    """Compose text and background from the element outward through ancestors."""
    front = parse_css_color(foreground)
    background = (0, 0, 0, 0)
    if front is None or not 0 <= foreground_opacity <= 1:
        return None
    front = front[:3] + (front[3] * foreground_opacity,)
    for layer in layers:
        color = parse_css_color(layer['color'])
        try:
            opacity = float(layer.get('opacity', 1))
        except (ValueError, TypeError):
            return None
        if color is None or not math.isfinite(opacity) or not 0 <= opacity <= 1:
            return None
        front, background = composite(front, color), composite(background, color)
        front = front[:3] + (front[3] * opacity,)
        background = background[:3] + (background[3] * opacity,)
    # The browser's default opaque canvas is white if no ancestor paints it.
    return composite(front, (1, 1, 1, 1)), composite(background, (1, 1, 1, 1))


def luminance(color):
    channels = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
                for channel in color[:3]]
    return sum(channel * weight for channel, weight in zip(channels, (.2126, .7152, .0722)))


def contrast_ratio(first, second):
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + .05) / (darker + .05)


def contrast_threshold(font_px, weight):
    # WCAG large text: 18pt regular or 14pt bold; CSS pixels are 96/72 pt.
    return 3 if font_px >= 24 or (font_px >= 14 * 96 / 72 and weight >= 700) else 4.5


def hex_color(color):
    return '#' + ''.join(f'{round(channel * 255):02x}' for channel in color[:3])


def summarize_text(samples):
    """Bound reports by semantic role, and keep unknown paint out of pass counts."""
    roles, below_threshold, small, unknown, exempt = {}, [], [], [], []
    for sample in samples:
        role = sample['role']
        bucket = roles.setdefault(role, {'samples': 0, 'qualified': 0, 'unknown': 0, 'exempt': 0,
            'below_threshold': 0, 'below_12px': 0, 'minimum_font_px': None,
            'minimum_contrast': None, 'worst_examples': []})
        bucket['samples'] += 1
        size = sample['font_px']
        bucket['minimum_font_px'] = size if bucket['minimum_font_px'] is None else min(size, bucket['minimum_font_px'])
        if size < 12:
            bucket['below_12px'] += 1
            if len(small) < 40:
                small.append(sample)
        if sample.get('exempt_reason'):
            bucket['exempt'] += 1
            if len(exempt) < 16:
                exempt.append({key: sample[key] for key in ('role', 'text', 'font_px', 'exempt_reason')})
            continue
        colors = None if sample.get('unknown_paint') else effective_colors(sample['color'], sample['layers'], sample.get('foreground_opacity', 1))
        if colors is None:
            bucket['unknown'] += 1
            if len(unknown) < 24:
                unknown.append({key: sample[key] for key in ('role', 'text', 'font_px', 'unknown_paint') if key in sample})
            continue
        ratio = contrast_ratio(*colors)
        minimum = contrast_threshold(size, sample['weight'])
        measured = {key: sample[key] for key in ('role', 'text', 'font_px', 'weight')}
        measured.update({'ratio': round(ratio, 3), 'threshold': minimum,
                         'foreground': hex_color(colors[0]), 'background': hex_color(colors[1])})
        bucket['qualified'] += 1
        bucket['minimum_contrast'] = round(ratio, 3) if bucket['minimum_contrast'] is None else min(round(ratio, 3), bucket['minimum_contrast'])
        bucket['worst_examples'].append(measured)
        bucket['worst_examples'] = sorted(bucket['worst_examples'], key=lambda row: row['ratio'])[:6]
        if ratio + 1e-8 < minimum:
            bucket['below_threshold'] += 1
            if len(below_threshold) < 60:
                below_threshold.append(measured)
    return {'roles': roles, 'below_threshold': below_threshold, 'small_text_examples': small,
            'unknown_examples': unknown, 'exempt_examples': exempt, 'sample_count': len(samples),
            'scope': 'Unique visible DOM text samples at bounded scroll positions; not a complete WCAG audit'}


TEXT_AUDIT_JS = r"""() => {
 const root=document.querySelector('.xlt'); if(!root)throw new Error('App shell is absent');
 const samples=[],skipped={hidden:0,offscreen:0,clipped:0,overlapped:0,nonText:0};
 const role=el=>el.closest('[role=tooltip],[class*=tooltip]')?'chart tooltip':
  el.closest('[data-testid*=legend],[class*=legend]')?'chart legend':
  el.closest('.xlt-sidebar,.xlt-nav')?'navigation':
  el.closest('h1,.xlt-header h2')?'page title':
  el.closest('thead,th')?'table header':el.closest('td')?'table body':
  el.closest('h2,h3,h4,.xlt-panel-head,.xlt-section-head')?'panel title':
  el.closest('small,label,.xlt-muted,.xlt-entity,.xlt-eyebrow,.xlt-notice,.xlt-quality')?'metadata':
  el.closest('svg')?'SVG label':el.closest('button,a,summary')?'control':'body';
 const intersects=(a,b)=>a.right>b.left&&a.left<b.right&&a.bottom>b.top&&a.top<b.bottom;
 const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT); let node,visited=0;
 while((node=walker.nextNode())&&visited++<30000&&samples.length<4000){
  const el=node.parentElement,text=node.nodeValue.trim(); if(!el||!text||el.closest('script,style,title,option')){skipped.nonText++;continue;}
  const style=getComputedStyle(el),ancestry=[]; let hidden=false,unknown=[];
  for(let cursor=el;cursor;cursor=cursor.parentElement){const css=getComputedStyle(cursor);
   if(css.display==='none'||css.visibility!=='visible'||Number(css.opacity)===0)hidden=true;
   ancestry.push({element:cursor,css});
   if(css.backgroundImage!=='none')unknown.push('background image / gradient');
   if(css.mixBlendMode!=='normal'||css.filter!=='none')unknown.push('blend / filter');
   if(ancestry.length>40){unknown.push('ancestor depth limit');break;}
  }
  if(hidden){skipped.hidden++;continue;}
  const range=document.createRange();range.selectNodeContents(node);const box=range.getBoundingClientRect();range.detach();
  if(box.width<=0||box.height<=0||!intersects(box,{left:0,top:0,right:innerWidth,bottom:innerHeight})){skipped.offscreen++;continue;}
  let clipped=false;
  for(const {element,css} of ancestry){if(element===document.body||element===document.documentElement)continue;
   if(css.overflowX!=='visible'||css.overflowY!=='visible'){
    const clip=element.getBoundingClientRect();if(!intersects(box,clip)){clipped=true;break;}
   }
  }
  if(clipped){skipped.clipped++;continue;}
  const x=Math.max(1,Math.min(innerWidth-1,(box.left+box.right)/2)),y=Math.max(1,Math.min(innerHeight-1,(box.top+box.bottom)/2));
  const hit=document.elementFromPoint(x,y);
  if(hit&&!el.contains(hit)&&!hit.contains(el)){skipped.overlapped++;continue;}
  const layers=ancestry.map(({css})=>({color:css.backgroundColor,opacity:css.opacity}));
  let color=style.color;
  if(el.closest('svg')){
   color=style.fill;
   const group=el.closest('g'),rects=group?[...group.querySelectorAll('rect')]:[];
   const behind=rects.filter(rect=>{const b=rect.getBoundingClientRect();return b.left<=x&&b.right>=x&&b.top<=y&&b.bottom>=y;})
    .sort((a,b)=>a.getBoundingClientRect().width*a.getBoundingClientRect().height-b.getBoundingClientRect().width*b.getBoundingClientRect().height)[0];
   if(behind){const css=getComputedStyle(behind);layers.unshift({color:css.fill,opacity:Number(css.opacity)*Number(css.fillOpacity)});}
   else unknown.push('SVG paint background needs manual review');
  }
  if(style.textShadow!=='none')unknown.push('text shadow');
  const svg=el.closest('svg'),matrix=svg&&typeof el.getScreenCTM==='function'?el.getScreenCTM():null;
  const scale=matrix?Math.hypot(matrix.c,matrix.d):1;
  samples.push({sample_id:visited,role:role(el),text:text.slice(0,180),font_px:parseFloat(style.fontSize)*scale,
   scope:el.closest('.xlt-resource-inspector')?'resource inspector':el.closest('.xlt-infra-resource-metrics')?'resource metrics':'page',
   weight:parseInt(style.fontWeight)||400,color,layers,foreground_opacity:svg?Number(style.fillOpacity):1,
   exempt_reason:el.closest('button:disabled,input:disabled,select:disabled,[aria-disabled=true]')?'Inactive control (contrast requirement exception)':null,
   unknown_paint:[...new Set(unknown)]});
 }
 return {samples,skipped,limited:visited>=30000||samples.length>=4000,
  canvas_count:root.querySelectorAll('canvas').length,svg_count:root.querySelectorAll('svg').length,
  document_overflow:document.documentElement.scrollWidth>innerWidth+2,
  height:Math.max(document.body.scrollHeight,document.documentElement.scrollHeight)};
}"""


def comparison_page(output, captures):
    sections = []
    for row in captures:
        if not row.get('screenshot'):
            continue
        header = escape(row['screen'] + ' · ' + row['viewport'] + ' · ' + row['theme'])
        images = ''.join('<figure><figcaption>' + caption + '</figcaption><img loading="lazy" src="'
                        + escape(row.get('selection_screenshot', row[key]) if key == 'screenshot' else row[key], quote=True) + '"></figure>'
                        for key, caption in (('reference_screenshot', 'Design reference'), ('screenshot', 'Actual synthetic Grafana'))
                        if row.get(key))
        sections.append('<section><h2>' + header + '</h2><div>' + images + '</div></section>')
    filename = 'comparison.html'
    (output / filename).write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>XLayer readability comparison</title><style>body{font:14px system-ui;margin:24px;background:#eef1f5;color:#172233}
section{padding:18px 0;border-top:1px solid #cdd6e1}section>div{display:flex;gap:12px}figure{margin:0;flex:1;min-width:0}
img{width:100%;height:auto}h2{font-size:16px}@media(max-width:650px){section>div{display:block}}</style>
<h1>Readability / topology comparison</h1><p>Same viewport dimensions. Reference values are fictional; actual Grafana uses synthetic telemetry.
DOM contrast sampling is bounded and excludes unsupported paint. Canvas axes, legends and tooltips require manual inspection.</p>'''
                             + ''.join(sections) + '</html>')
    return filename


SCROLL_TARGET_JS = """
 const root=document.querySelector('.xlt');
 function target(){
  for(let element=root;element&&element!==document.body&&element!==document.documentElement;element=element.parentElement){
   const overflow=getComputedStyle(element).overflowY;
   if(/^(auto|scroll|overlay)$/.test(overflow)&&element.scrollHeight>element.clientHeight+2)return element;
  }
  return document.scrollingElement||document.documentElement;
 }
 const element=target(),documentTarget=element===document.scrollingElement||element===document.documentElement;
"""


def scroll_info(page):
    return page.evaluate('() => {' + SCROLL_TARGET_JS + """
     return {kind:documentTarget?'document':'App ancestor',tag:element.tagName,
       class:element.className,scrollHeight:element.scrollHeight,
       clientHeight:documentTarget?innerHeight:element.clientHeight,
       scrollTop:documentTarget?scrollY:element.scrollTop};
    }""")


def scroll_to(page, position):
    return page.evaluate('(position) => {' + SCROLL_TARGET_JS + """
     if(documentTarget)window.scrollTo({top:position,left:scrollX,behavior:'instant'});
     else element.scrollTo({top:position,left:element.scrollLeft,behavior:'instant'});
     return documentTarget?scrollY:element.scrollTop;
    }""", position)


def reference_theme(page):
    return page.locator('body').evaluate("""body=>body.dataset.theme||
      (body.classList.contains('dark')?'dark':'light')""")


def topology_checks(page, context_reader=None, changes=None, redact=str):
    """Use explicit UI controls; do not infer topology health from scrape success."""
    result = {'scope': 'Configured relationships and declared component identity, not physical connectivity'}
    compact = page.get_by_role('button', name='Compact', exact=True)
    expanded = page.get_by_role('button', name='Expanded', exact=True)
    cards = page.locator('.xlt-cluster-map-node,.xlt-node-card,.xlt-topology-node')
    if compact.count() and expanded.count():
        compact.click()
        result['compact_node_cards'] = cards.count()
        expanded.click()
        result['expanded_node_cards'] = cards.count()
        compact.click()
        result['mode_switch_available'] = True
    else:
        result['mode_switch_available'] = False
    ledger = page.locator('details').filter(has=page.locator('summary').filter(has_text=re.compile('Source Ledger|Relationship Ledger')))
    if ledger.count():
        ledger.first.locator('summary').click()
        result['source_ledger'] = redact(ledger.first.inner_text())[:2000]
        result['ledger_declared_scope_visible'] = bool(re.search('configured|declar|unverified|not observed', result['source_ledger'], re.I))
        ledger.first.locator('summary').click()
    identities = cards.evaluate_all("""nodes=>nodes.map(node=>({name:node.getAttribute('aria-label')||'',
        identity:node.getAttribute('data-resource-key')||node.getAttribute('data-node-key')||'',
        kind:node.getAttribute('data-kind')||''})).slice(0,256)""")
    groups = {}
    for value in identities:
        groups.setdefault(value['name'], []).append(value)
    duplicates = [values for values in groups.values() if len(values) > 1]
    result['same_name_components'] = duplicates
    result['duplicate_identity_verified'] = all(all(row['identity'] for row in group)
        and len({row['identity'] for row in group}) == len(group) for group in duplicates) if duplicates else None
    result['identity_note'] = 'No duplicates in fixture' if not duplicates else 'Explicit DOM identities checked; absent identity stays unverified'
    if cards.count() and context_reader and changes:
        before = context_reader(page.url)
        first = cards.first
        declared_key = first.get_attribute('data-resource-key') or first.get_attribute('data-node-key')
        try:
            identity = json.loads(declared_key)
        except (ValueError, TypeError):
            identity = None
        first.click()
        inspector = page.get_by_role('complementary', name='Resource inspector', exact=True)
        if isinstance(identity, list) and len(identity) == 3:
            inspector.get_by_role('heading', name='Selected Resource · ' + str(identity[2]), exact=True).wait_for(timeout=15000)
            page.wait_for_function("""expected=>{
              const inspector=document.querySelector('[aria-label="Resource inspector"]');
              if(!inspector)return false;
              const fields=Object.fromEntries([...inspector.querySelectorAll('dl div')].map(row=>[
                row.querySelector('dt')?.textContent?.trim(),row.querySelector('dd')?.textContent?.trim()]));
              return fields.Cluster===String(expected[0])&&fields.Namespace===String(expected[1])&&fields.Identity===String(expected[2]);
            }""", arg=identity, timeout=15000)
        else:
            inspector.get_by_role('heading', name=re.compile('^Selected Resource · ')).wait_for(timeout=15000)
        fields = inspector.locator('dl').evaluate("""dl=>Object.fromEntries([...dl.querySelectorAll('div')]
          .map(row=>[row.querySelector('dt')?.textContent?.trim(),row.querySelector('dd')?.textContent?.trim()])
          .filter(pair=>pair[0]))""")
        after = context_reader(page.url)
        preserved = {key: value for key, value in before.items() if key in (
            'from', 'to', 'timezone', 'var-run_id', 'var-record_id', 'var-source_node', 'var-worker', 'var-phase_worker')}
        result['selection'] = {'declared_key': declared_key, 'inspector': fields,
            'investigation_context_changes': changes(preserved, after),
            'infra_component_matches': after.get('var-infra_component') == [declared_key]}
        if isinstance(identity, list) and len(identity) == 3:
            result['selection']['declared_identity_matches'] = (
                fields.get('Cluster') == str(identity[0]) and fields.get('Namespace') == str(identity[1])
                and fields.get('Identity') == str(identity[2]))
        if fields.get('Mapping') == 'configured' and fields.get('Resource node') not in (None, 'Unknown'):
            result['selection']['resource_node_matches'] = after.get('var-node') == [fields['Resource node']]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--context', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mockup', type=Path, help='B2 Infrastructure HTML reference, kept unchanged')
    parser.add_argument('--browser')
    parser.add_argument('--iteration', type=int, required=True)
    parser.add_argument('--screens')
    parser.add_argument('--report-only', action='store_true')
    parser.add_argument('--require-contrast', action='store_true', help='Fail when qualified DOM samples miss their contrast threshold; unknown paint stays separate')
    args = parser.parse_args()
    from design_validate import owned_origin, selected_screens, selected_context, app_url, context_changes
    try:
        origin, screens = owned_origin(args.url), selected_screens(args.screens)
        context = selected_context(args.url, json.loads(args.context.read_text()) if args.context else None)
        if args.iteration < 1 or (args.mockup and not args.mockup.is_file()):
            raise ValueError('Use a positive iteration and an existing reference HTML')
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if not context:
        context = {'from': ['now-5m'], 'to': ['now'], 'var-cluster': ['scenes-demo'],
                   'var-run_id': ['verl-agent-demo'], 'var-node': ['gpu-node-0'], 'var-gpu': ['0']}
    output = args.output / f'iteration-{args.iteration}'
    output.mkdir(parents=True, exist_ok=False)
    from playwright.sync_api import sync_playwright
    from browser_validate import EntryDiagnostics, diagnostic_text
    captures, failures = [], []
    manifest = {'iteration': args.iteration, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'data_origin': 'Synthetic observations through actual Grafana / Prometheus / Loki',
        'reference_sha256': hashlib.sha256(args.mockup.read_bytes()).hexdigest() if args.mockup else None,
        'viewports': VIEWPORTS, 'captures': captures, 'failures': failures,
        'limitations': ['No WCAG certification; bounded visible DOM sampling, not all rendered text',
            'Canvas axes/legends/tooltips and complex paint need manual screenshot inspection',
            'Synthetic data does not validate physical GPU, network or storage-cluster health',
            'Below 12px is a readability observation, not a WCAG font-size requirement']}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'],
                    **({'executable_path': args.browser} if args.browser else {}))
        try:
            for viewport, size in VIEWPORTS.items():
                for theme in ('light', 'dark'):
                    page = browser.new_page(viewport=size, color_scheme=theme, locale='en-US', timezone_id='UTC')
                    errors = []
                    page.on('pageerror', lambda error, target=errors: target.append(diagnostic_text(error, 1000)) if len(target) < 20 else None)
                    diagnostics = EntryDiagnostics(page, output, viewport + '-' + theme, errors)
                    for screen in screens:
                        stem = screen + '-' + viewport + '-' + theme
                        row = {'screen': screen, 'viewport': viewport, 'theme': theme, 'screenshot': stem + '.png'}
                        errors.clear()
                        diagnostics.query_responses.clear()
                        diagnostics.http_failures.clear()
                        diagnostics.console_errors.clear()
                        try:
                            page.goto(app_url(origin, screen, context, theme))
                            page.locator('.xlt').wait_for(state='visible', timeout=30000)
                            page.wait_for_timeout(1300)
                            close_menu = page.get_by_role('button', name='Close menu', exact=True)
                            if close_menu.count() and close_menu.first.is_visible():
                                close_menu.first.click()
                                page.wait_for_timeout(150)
                                row['grafana_menu_closed'] = True
                            row['context_changes'] = context_changes(context, selected_context(page.url))
                            row['theme_observed'] = page.locator('.xlt').get_attribute('data-theme')
                            page.screenshot(path=str(output / row['screenshot']), full_page=False)
                            samples, skipped, canvases = {}, {}, 0
                            initial = page.evaluate(TEXT_AUDIT_JS)
                            scroll = scroll_info(page)
                            maximum = max(0, scroll['scrollHeight'] - scroll['clientHeight'])
                            positions = sorted({0, round(maximum / 2), maximum})
                            reached = []
                            for position in positions:
                                reached.append(scroll_to(page, position))
                                page.wait_for_timeout(100)
                                audit = page.evaluate(TEXT_AUDIT_JS)
                                for sample in audit['samples']:
                                    sample['text'] = diagnostic_text(sample['text'], 180)
                                    key = (sample['sample_id'], sample['text'], sample['font_px'], sample['color'])
                                    samples[key] = sample
                                for reason, count in audit['skipped'].items():
                                    skipped[reason] = skipped.get(reason, 0) + count
                                canvases = max(canvases, audit['canvas_count'])
                            scroll_to(page, 0)
                            row['readability'] = summarize_text(list(samples.values()))
                            row['readability'].update({'scroll_positions': positions, 'scroll_reached': reached,
                                'scroll_target': scroll, 'skipped': skipped, 'canvas_count': canvases})
                            qualified = sum(role['qualified'] for role in row['readability']['roles'].values())
                            below = sum(role['below_threshold'] for role in row['readability']['roles'].values())
                            if args.require_contrast and (below or not qualified):
                                failures.append({'screen': stem, 'check': 'qualified DOM contrast',
                                    'below_threshold': below, 'qualified_samples': qualified})
                            row['document_overflow'] = initial['document_overflow']
                            row['quality'] = page.locator('.xlt').evaluate("""root=>{const text=root.innerText.toLowerCase();return {
                              visible_labels:['missing','stale','unknown','query failure','query error','no data','measured zero','shared','sampled','configured','observed','unavailable'].filter(word=>text.includes(word)),
                              semantics_note:'Labels are rendered states; they do not verify source accuracy or health'}}""")
                            if screen == 'infrastructure':
                                row['topology'] = topology_checks(page, selected_context, context_changes, diagnostic_text)
                                selection = row['topology'].get('selection', {})
                                if selection.get('investigation_context_changes') or any(
                                        selection.get(key) is False for key in ('infra_component_matches', 'declared_identity_matches', 'resource_node_matches')):
                                    failures.append({'screen': stem, 'check': 'topology resource selection', 'details': selection})
                                if selection:
                                    scroll_to(page, 0)
                                    page.wait_for_timeout(150)
                                    row['selection_screenshot'] = stem + '-selected.png'
                                    page.screenshot(path=str(output / row['selection_screenshot']), full_page=False)
                                    selected_samples = page.evaluate(TEXT_AUDIT_JS)['samples']
                                    selected_samples = [sample for sample in selected_samples if sample.get('scope') in ('resource inspector', 'resource metrics')]
                                    for sample in selected_samples:
                                        sample['text'] = diagnostic_text(sample['text'], 180)
                                    row['selected_resource_readability'] = summarize_text(selected_samples)
                                    selected_below = sum(role['below_threshold'] for role in row['selected_resource_readability']['roles'].values())
                                    if args.require_contrast and selected_below:
                                        failures.append({'screen': stem, 'check': 'selected resource DOM contrast', 'below_threshold': selected_below})
                            button = page.get_by_role('button', name='Switch to ' + ('Dark' if theme == 'light' else 'Light') + ' theme', exact=True)
                            if button.count():
                                before = selected_context(page.url)
                                button.click()
                                page.locator('.xlt[data-theme="' + ('dark' if theme == 'light' else 'light') + '"]').wait_for(timeout=15000)
                                row['theme_toggle_context_changes'] = context_changes(before, selected_context(page.url))
                                page.get_by_role('button', name='Switch to ' + theme.title() + ' theme', exact=True).click()
                                page.locator('.xlt[data-theme="' + theme + '"]').wait_for(timeout=15000)
                            else:
                                row['theme_toggle_unavailable'] = True
                            if args.mockup and screen == 'infrastructure':
                                reference = browser.new_page(viewport=size, color_scheme=theme)
                                try:
                                    reference.goto(args.mockup.resolve().as_uri())
                                    if reference_theme(reference) != theme:
                                        toggle = reference.locator('#theme,#theme-toggle,[aria-label="테마 전환"],button[title*="테마"]')
                                        if toggle.count():
                                            toggle.first.click()
                                    row['reference_theme_observed'] = reference_theme(reference)
                                    if row['reference_theme_observed'] != theme:
                                        row['reference_theme_unverified'] = True
                                    row['reference_screenshot'] = 'reference-' + stem + '.png'
                                    reference.screenshot(path=str(output / row['reference_screenshot']), full_page=False)
                                finally:
                                    reference.close()
                            for key in ('context_changes', 'theme_toggle_context_changes', 'document_overflow'):
                                if row.get(key):
                                    failures.append({'screen': stem, 'check': key, 'details': row[key]})
                            if row['theme_observed'] != theme:
                                failures.append({'screen': stem, 'check': 'theme', 'observed': row['theme_observed']})
                        except Exception as error:
                            row['failure'] = diagnostic_text(error, 2000)
                            failures.append({'screen': stem, 'check': 'rendering/navigation', 'error': row['failure']})
                            diagnostics.label = stem
                            diagnostics.fail(error)
                        row['browser_errors'] = list(errors)
                        row['console_errors'] = list(diagnostics.console_errors)
                        row['http_failures'] = list(diagnostics.http_failures)
                        row['datasource_responses'] = list(diagnostics.query_responses)
                        if errors:
                            failures.append({'screen': stem, 'check': 'browser errors', 'errors': list(errors)})
                        captures.append(row)
                    page.close()
        finally:
            browser.close()
            manifest['comparison_html'] = comparison_page(output, captures)
            manifest['complete'] = len(captures) == len(screens) * len(VIEWPORTS) * 2 and not failures
            audits = [audit for row in captures for audit in (row.get('readability', {}), row.get('selected_resource_readability', {}))]
            manifest['contrast_below_threshold_count'] = sum(sum(role['below_threshold'] for role in audit.get('roles', {}).values()) for audit in audits)
            manifest['below_12px_count'] = sum(sum(role['below_12px'] for role in audit.get('roles', {}).values()) for audit in audits)
            manifest['qualified_dom_sample_count'] = sum(sum(role['qualified'] for role in audit.get('roles', {}).values()) for audit in audits)
            manifest['qualified_dom_contrast_passed'] = bool(manifest['qualified_dom_sample_count']) and manifest['contrast_below_threshold_count'] == 0 and all(
                not row.get('failure') and any(role['qualified'] for role in row.get('readability', {}).get('roles', {}).values()) for row in captures)
            manifest['require_contrast'] = args.require_contrast
            manifest['readability_scope'] = 'Only qualified visible DOM samples; unknown paint, inactive controls and Canvas text are outside the pass count'
            (output / 'validation.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'iteration': args.iteration, 'captures': len(captures), 'failures': len(failures),
        'contrast_below_threshold': manifest['contrast_below_threshold_count'], 'below_12px': manifest['below_12px_count'],
        'manifest': str(output / 'validation.json')}))
    if failures and not args.report_only:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
