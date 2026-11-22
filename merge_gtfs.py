#!/usr/bin/env python3
"""
Fusiona varios GTFS en uno solo, con identificadores prefijados por fuente.

Uso:
  python3 merge_gtfs.py --out /ruta/gtfs_all \
      tfi|/tmp/yukie/work/gtfs/all \
      nibus|/tmp/yukie/work/ni_gtfs_bus \
      nirail|/tmp/yukie/work/ni_gtfs_rail

Cada fuente aporta su prefijo a stop_id, route_id, trip_id y service_id, de
modo que dos redes con el mismo código de parada (Dundalk existe en TFI y en
Translink) no se pisan. El agency_id se conserva tal cual, porque el generador
lo usa para identificar al operador; si dos fuentes traen el mismo, se
prefija la segunda.
"""
import argparse
import csv
import os

FILES = {
    'agency.txt': ['agency_id', 'agency_name', 'agency_url', 'agency_timezone'],
    'stops.txt': ['stop_id', 'stop_name', 'stop_lat', 'stop_lon', 'location_type'],
    'routes.txt': ['route_id', 'agency_id', 'route_short_name', 'route_long_name', 'route_type'],
    'trips.txt': ['route_id', 'service_id', 'trip_id', 'trip_headsign', 'trip_short_name'],
    'stop_times.txt': ['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence'],
    'calendar.txt': ['service_id', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
                     'saturday', 'sunday', 'start_date', 'end_date'],
}


def read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('sources', nargs='+', help='prefijo|ruta_gtfs')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    out = {name: [] for name in FILES}
    used_agencies = set()
    agency_by_source = {}

    for spec in args.sources:
        prefix, path = spec.split('|', 1)
        pfx = prefix + '_'
        if not os.path.isdir(path):
            print('  aviso: no existe %s' % path)
            continue

        # agencias: se conserva el id salvo colisión entre fuentes
        remap = {}
        for a in read(os.path.join(path, 'agency.txt')):
            aid = a.get('agency_id') or prefix
            if aid in used_agencies:
                aid = pfx + aid
            used_agencies.add(aid)
            remap[a.get('agency_id') or prefix] = aid
            out['agency.txt'].append({
                'agency_id': aid,
                'agency_name': a.get('agency_name', ''),
                'agency_url': a.get('agency_url', ''),
                'agency_timezone': a.get('agency_timezone') or 'Europe/Dublin',
            })
        agency_by_source[prefix] = remap

        for s in read(os.path.join(path, 'stops.txt')):
            out['stops.txt'].append({
                'stop_id': pfx + s['stop_id'],
                'stop_name': s.get('stop_name', ''),
                'stop_lat': s.get('stop_lat', ''),
                'stop_lon': s.get('stop_lon', ''),
                'location_type': s.get('location_type', '0'),
            })

        for r in read(os.path.join(path, 'routes.txt')):
            out['routes.txt'].append({
                'route_id': pfx + r['route_id'],
                'agency_id': remap.get(r.get('agency_id'), r.get('agency_id', '')),
                'route_short_name': r.get('route_short_name', ''),
                'route_long_name': r.get('route_long_name', ''),
                'route_type': r.get('route_type', '3'),
            })

        for c in read(os.path.join(path, 'calendar.txt')):
            out['calendar.txt'].append({
                'service_id': pfx + c['service_id'],
                **{k: c.get(k, '') for k in FILES['calendar.txt'][1:]},
            })

        for t in read(os.path.join(path, 'trips.txt')):
            out['trips.txt'].append({
                'route_id': pfx + t['route_id'],
                'service_id': pfx + t['service_id'],
                'trip_id': pfx + t['trip_id'],
                'trip_headsign': t.get('trip_headsign', ''),
                'trip_short_name': t.get('trip_short_name', ''),
            })

        for st in read(os.path.join(path, 'stop_times.txt')):
            out['stop_times.txt'].append({
                'trip_id': pfx + st['trip_id'],
                'arrival_time': st.get('arrival_time', ''),
                'departure_time': st.get('departure_time', ''),
                'stop_id': pfx + st['stop_id'],
                'stop_sequence': st.get('stop_sequence', ''),
            })

        print('  %-8s %6d paradas %6d rutas %7d viajes'
              % (prefix, len(out['stops.txt']), len(out['routes.txt']), len(out['trips.txt'])))

    for name, header in FILES.items():
        with open(os.path.join(args.out, name), 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=header)
            w.writeheader()
            for row in out[name]:
                w.writerow({k: row.get(k, '') for k in header})
        print('%-16s %d filas' % (name, len(out[name])))


if __name__ == '__main__':
    main()