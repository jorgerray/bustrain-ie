#!/usr/bin/env python3
"""
Descarga y prepara las entradas de Bus&Train Irlanda + Irlanda del Norte.

  GTFS nacional de TFI        -> Mobility Database (mdb-2364)
  Translink (Metro, Ulsterbus,
  NI Railways)                -> OpenDataNI (TransXchange) -> GTFS con txc_to_gtfs.py
  Callejero                   -> GeoNames IE + GB (solo Irlanda del Norte)

Salida: --out/gtfs (GTFS fusionado), --out/IE_NI.txt (callejero), --out/raw (ZIP originales)
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
import zipfile

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/152.0.0 Safari/537.36')
TFI_URL = 'https://files.mobilitydatabase.org/mdb-2364/latest.zip'
CKAN = 'https://admin.opendatani.gov.uk/api/3/action/package_search?q=translink&rows=50'

# título del dataset en OpenDataNI -> (prefijo de agencia, tipo de ruta GTFS, etiqueta)
NI_DATASETS = {
    'Translink Metro & Glider Timetable Data.': ('metro', '3', 'Metro & Glider'),
    'Ulsterbus and Goldliner Timetable Data': ('ulster', '3', 'Ulsterbus & Goldliner'),
    'Translink Northern Ireland Rail Timetable Data': ('nirail', '2', 'NI Railways'),
}
HERE = os.path.dirname(os.path.abspath(__file__))


def get(url, dest, binary=True):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, 'wb' if binary else 'w') as fh:
        fh.write(r.read())
    return dest


def latest_zip(datasets):
    """URL del ZIP más reciente de cada dataset de Translink."""
    req = urllib.request.Request(CKAN, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)['result']['results']
    out = {}
    for p in data:
        key = NI_DATASETS.get(p['title'])
        if not key:
            continue
        zips = [r for r in p.get('resources', []) if r.get('format') == 'ZIP']
        if not zips:
            continue
        zips.sort(key=lambda r: r.get('last_modified') or r.get('created') or '', reverse=True)
        out[key[0]] = zips[0]['url']
    return out


def geonames(dest):
    """IE.txt + las localidades de Irlanda del Norte de GB.txt."""
    for code in ('IE', 'GB'):
        z = os.path.join(dest, code + '.zip')
        get('https://download.geonames.org/export/dump/%s.zip' % code, z)
        with zipfile.ZipFile(z) as zf:
            zf.extract(code + '.txt', dest)
    ie = os.path.join(dest, 'IE.txt')
    gb = os.path.join(dest, 'GB.txt')
    merged = os.path.join(dest, 'IE_NI.txt')
    n = 0
    with open(merged, 'w', encoding='utf-8') as out, open(ie, encoding='utf-8') as a:
        for line in a:
            out.write(line)
    with open(gb, encoding='utf-8') as b, open(merged, 'a', encoding='utf-8') as out:
        for line in b:
            f = line.rstrip('\n').split('\t')
            if len(f) > 10 and f[10] == 'NIR':
                out.write(line)
                n += 1
    return merged, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(HERE, 'inputs'))
    args = ap.parse_args()
    raw = os.path.join(args.out, 'raw')
    os.makedirs(raw, exist_ok=True)

    print('TFI ...', flush=True)
    tfi = get(TFI_URL, os.path.join(raw, 'tfi.zip'))
    tfi_dir = os.path.join(args.out, 'tfi')
    os.makedirs(tfi_dir, exist_ok=True)
    with zipfile.ZipFile(tfi) as zf:
        zf.extractall(tfi_dir)

    print('Callejero ...', flush=True)
    gaz, n = geonames(args.out)
    print('  %s (%d localidades de Irlanda del Norte)' % (gaz, n))

    print('Translink ...', flush=True)
    urls = latest_zip(NI_DATASETS)
    specs = []
    for key, (prefix, rtype, label) in NI_DATASETS.items():
        url = urls.get(prefix)
        if not url:
            sys.exit('No encuentro el ZIP de %s en OpenDataNI' % label)
        dest = os.path.join(raw, 'ni_%s.zip' % prefix)
        get(url, dest)
        print('  %-22s %s' % (label, url.split('/')[-1]))
        specs.append('%s|%s|%s|%s|https://www.translink.co.uk/' % (prefix, rtype, dest, label))

    print('Convirtiendo TransXchange a GTFS ...', flush=True)
    bus_dir = os.path.join(args.out, 'ni_bus')
    rail_dir = os.path.join(args.out, 'ni_rail')
    subprocess.run([sys.executable, os.path.join(HERE, 'txc_to_gtfs.py'), '--out', bus_dir] + specs[:2], check=True)
    subprocess.run([sys.executable, os.path.join(HERE, 'txc_to_gtfs.py'), '--out', rail_dir, specs[2]], check=True)

    print('Fusionando ...', flush=True)
    merged = os.path.join(args.out, 'gtfs')
    subprocess.run([sys.executable, os.path.join(HERE, 'merge_gtfs.py'), '--out', merged,
                    'tfi|' + tfi_dir, 'nibus|' + bus_dir, 'nirail|' + rail_dir], check=True)
    print('Listo: %s' % merged)


if __name__ == '__main__':
    main()