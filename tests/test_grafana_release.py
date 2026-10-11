"""One pinned Grafana release drives config, managed paths and browser tools."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_default_managed_config_targets_grafana_13_and_preserves_toggle_opt_out(tmp_path,monkeypatch):
    from xlayer_telemetry.operations.config import load_config
    monkeypatch.delenv('GRAFANA_VERSION',raising=False)
    monkeypatch.delenv('GF_FEATURE_TOGGLES_ENABLE',raising=False)
    path=tmp_path/'config.toml';path.write_text('[telemetry]\n')
    config,_=load_config(path)
    assert config['GRAFANA_VERSION']=='13.1.7'
    assert config['GF_FEATURE_TOGGLES_ENABLE']=='vizLegendFacetedFilter'
    path.write_text('[telemetry]\nGRAFANA_VERSION="12.1.0"\nGF_FEATURE_TOGGLES_ENABLE=""\n')
    config,_=load_config(path)
    assert config['GRAFANA_VERSION']=='12.1.0' and config['GF_FEATURE_TOGGLES_ENABLE']==''


@pytest.mark.parametrize('version',['13.2.0','../grafana','13.1.8',''])
def test_unpinned_managed_release_is_rejected(tmp_path,monkeypatch,version):
    from xlayer_telemetry.operations.config import ConfigError,load_config
    monkeypatch.delenv('GRAFANA_VERSION',raising=False)
    path=tmp_path/'config.toml';path.write_text('[telemetry]\nGRAFANA_VERSION='+json.dumps(version)+'\n')
    with pytest.raises(ConfigError,match='GRAFANA_VERSION'):load_config(path)


def test_shell_release_lookup_and_offline_app_detection_share_managed_path(tmp_path,monkeypatch):
    from xlayer_telemetry.grafana_release import grafana_home,release
    from xlayer_telemetry.operations.app import detected_version
    monkeypatch.setattr('xlayer_telemetry.operations.app.api',lambda *_:None)
    tools=tmp_path/'tools with spaces'
    for version in ('12.1.0','13.1.7'):
        home=grafana_home(tools,version)
        binary=home/'bin/grafana';binary.parent.mkdir(parents=True)
        binary.write_text(f'#!/bin/sh\necho "Version {version}"\n');binary.chmod(0o755)
        result=subprocess.run([sys.executable,'-m','xlayer_telemetry.grafana_release','--version',version,
                               '--home',str(tools)],cwd=ROOT,capture_output=True,text=True,check=True)
        assert result.stdout.strip()==str(home)
        assert detected_version({'TOOLS_DIR':str(tools),'GRAFANA_VERSION':version})==version
        for arch in ('amd64','arm64'):
            metadata=release(version,arch)
            assert len(metadata['sha256'])==64 and metadata['url'].startswith('https://dl.grafana.com/')


def test_many_series_panels_enable_native_visibility_filter_without_changing_legend_layout():
    panels=[]
    for filename in ('compute-communication.json','data-storage.json'):
        dashboard=json.loads((ROOT/'examples/dashboards'/filename).read_text())
        panels.extend(p for p in dashboard['panels'] if p['type']=='timeseries')
    selected=[p for p in panels if any(any(metric in t.get('expr','') for metric in (
        'telemetry_gpu_utilization_percent','node_disk_read_bytes_total','node_disk_read_time_seconds_total',
        'node_network_receive_bytes_total','node_infiniband_port_data_received_bytes_total')) for t in p.get('targets',[]))]
    assert len(selected)>=5
    for panel in selected:
        legend=panel['options']['legend']
        assert legend['enableFacetedFilter'] is True and legend['showLegend'] is True
        assert legend['placement'] in ('right','bottom')
        assert legend['displayMode'] in ('list','table')


def test_dense_resource_panels_keep_visible_lines_and_compact_legends():
    for filename,ids in (('compute-communication.json',(2,8,9)),('data-storage.json',(1,30,114,115))):
        dashboard=json.loads((ROOT/'examples/dashboards'/filename).read_text())
        for panel in dashboard['panels']:
            if panel['id'] not in ids:continue
            defaults=panel['fieldConfig']['defaults']
            assert defaults['custom']['fillOpacity']==0
            assert defaults['custom']['showPoints']=='never'
            assert defaults['custom']['lineWidth']==1
            assert panel['options']['legend']['limit']==8
            assert panel['options']['tooltip']['mode']=='single'
            assert defaults['color']['mode']=='palette-classic-by-name'


def test_compatibility_mode_keeps_existing_scenes_bundle_on_grafana_13():
    from xlayer_telemetry.grafana_release import compatibility_config
    assert 'react19 = false' in compatibility_config('13.1.7')
    assert 'react19' not in compatibility_config('12.1.0')
