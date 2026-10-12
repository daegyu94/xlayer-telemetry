"""Pinned managed Grafana releases; one source for CLI, shell and browser CI."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

DEFAULT_GRAFANA_VERSION='13.1.7'
LEGEND_TOGGLE='vizLegendFacetedFilter'
# Publisher checksums from the official download page and .tar.gz.sha256 files.
# https://grafana.com/grafana/download/13.1.7?edition=oss&platform=linux
_RELEASES={
    '13.1.7': {
        'url':'https://dl.grafana.com/grafana/release/13.1.7/grafana_13.1.7_36414001255_linux_{arch}.tar.gz',
        'home':'grafana-13.1.7',
        'sha256':{'amd64':'7c168c1fe147e5f3550fa15e5f59362bde5719ab98c905a91a37aa969203c92f',
                  'arm64':'25a9f6d3eb090c6520a76fae2b20a2574e115b1faa40da8b0747e7b7211f6536'},
    },

}


def release(version=None,arch='amd64'):
    version=DEFAULT_GRAFANA_VERSION if version is None else version
    if version not in _RELEASES or arch not in {'amd64','arm64'}:
        raise ValueError('GRAFANA_VERSION must be 13.1.7; other versions are unsupported; architecture must be amd64 or arm64')
    entry=_RELEASES[version]
    return {'version':version,'home':entry['home'],'url':entry['url'].format(arch=arch),'sha256':entry['sha256'][arch]}


def grafana_home(tools,version=None):
    return Path(tools)/release(version)['home']


def default_toggles(version=None):
    release(version)
    return LEGEND_TOGGLE

def compatibility_config(version=None):
    # Grafana 13 ships both runtimes. The existing Scenes bundle embeds React
    # 18 JSX helpers; use Grafana's official runtime flag without changing UI.
    release(version)
    return '[feature_toggles]\nreact19 = false\n'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',default=os.environ.get('GRAFANA_VERSION') or DEFAULT_GRAFANA_VERSION)
    output=parser.add_mutually_exclusive_group(required=True)
    output.add_argument('--home',type=Path)
    output.add_argument('--archive',choices=('amd64','arm64'))
    output.add_argument('--feature-toggles',action='store_true')
    output.add_argument('--compat-config',action='store_true')
    args=parser.parse_args()
    try:
        if args.home:print(grafana_home(args.home,args.version))
        elif args.archive:
            entry=release(args.version,args.archive)
            print(entry['url']+'\t'+entry['sha256'])
        elif args.compat_config:print(compatibility_config(args.version),end='')
        else:print(default_toggles(args.version))
    except ValueError as error:parser.error(str(error))
