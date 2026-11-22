#!/usr/bin/env python3
"""
Convierte horarios TransXchange (Translink, Irlanda del Norte) a GTFS.

Entrada : uno o varios .zip o .xml de TransXchange (bus Metro/Glider, Ulsterbus/
          Goldliner y NIR Rail + Enterprise).
Salida  : un GTFS en la carpeta indicada (agency, stops, routes, trips,
          stop_times, calendar), listo para build_data.py.

TransXchange 2.4 usa un espacio de nombres; aquí se ignora y se trabaja por
nombre local de etiqueta. Las excepciones de calendario (festivos, días
servidos por organización) no se modelan: el calendario es el patrón semanal,
que es lo que necesita el buscador.
"""
import argparse
import csv
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

DAY_TAGS = {
    'monday': [1, 0, 0, 0, 0, 0, 0],
    'tuesday': [0, 1, 0, 0, 0, 0, 0],
    'wednesday': [0, 0, 1, 0, 0, 0, 0],
    'thursday': [0, 0, 0, 1, 0, 0, 0],
    'friday': [0, 0, 0, 0, 1, 0, 0],
    'saturday': [0, 0, 0, 0, 0, 1, 0],
    'sunday': [0, 0, 0, 0, 0, 0, 1],
    'mondaytofriday': [1, 1, 1, 1, 1, 0, 0],
    'mondaytosaturday': [1, 1, 1, 1, 1, 1, 0],
    'mondaytosunday': [1, 1, 1, 1, 1, 1, 1],
    'mondaytothursday': [1, 1, 1, 1, 0, 0, 0],
    'fridaytosaturday': [0, 0, 0, 0, 1, 1, 0],
    'saturdaytosunday': [0, 0, 0, 0, 0, 1, 1],
    'notsaturday': [1, 1, 1, 1, 1, 0, 1],
    'notsunday': [1, 1, 1, 1, 1, 1, 0],
    'notmonday': [0, 1, 1, 1, 1, 1, 1],
    'notfriday': [1, 1, 1, 1, 0, 1, 1],
    'notmondaytofriday': [0, 0, 0, 0, 0, 1, 1],
}


def local(tag):
    return tag.rsplit('}', 1)[-1]


def find_local(el, name):
    """Primer descendiente con ese nombre local (el XML usa espacio de nombres)."""
    if el is None:
        return None
    for child in el.iter():
        if local(child.tag) == name:
            return child
    return None


def text(el, path):
    if el is None:
        return ''
    node = el.find(path)
    if node is None:
        # búsqueda por nombre local, por si el árbol trae espacios mezclados
        want = path.split('/')[-1]
        for child in el.iter():
            if local(child.tag) == want and child.text:
                return child.text.strip()
        return ''
    return (node.text or '').strip()


def duration_min(value):
    """'PT2M' / 'PT1H5M' / 'PT90S' -> minutos."""
    if not value:
        return 0
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', value.strip())
    if not m:
        return 0
    h, mi, s = m.groups()
    return int(h or 0) * 60 + int(mi or 0) + (float(s) / 60.0 if s else 0)


def clock(value):
    """'07:17:00' -> minutos desde medianoche (admite 25:10)."""
    if not value:
        return None
    parts = value.strip().split(':')
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None


def fmt(minutes):
    minutes = int(round(minutes))
    return '%02d:%02d:00' % (minutes // 60, minutes % 60)


def days_from(profile):
    """Días de la semana de un OperatingProfile -> lista de 7 ceros/unos."""
    if profile is None:
        return [1, 1, 1, 1, 1, 1, 1]
    for node in profile.iter():
        if local(node.tag) != 'DaysOfWeek':
            continue
        days = [0] * 7
        found = False
        for child in node:
            name = local(child.tag).lower()
            if name in DAY_TAGS:
                found = True
                for i, v in enumerate(DAY_TAGS[name]):
                    days[i] = days[i] or v
        if found:
            return days
    return [1, 1, 1, 1, 1, 1, 1]


def iter_xml(path):
    """Recorre el XML sin cargarlo entero."""
    with open(path, 'rb') as fh:
        for event, el in ET.iterparse(fh, events=('end',)):
            yield local(el.tag), el


def read_transxchange(path, agency_id, route_type, prefix):
    """Devuelve stops, services, patterns y journeys de un fichero TXC."""
    stops, services, patterns, journeys = {}, {}, {}, []
    operators = {}
    for tag, el in iter_xml(path):
        if tag == 'StopPoint':
            code = text(el, 'AtcoCode')
            if not code:
                el.clear()
                continue
            name = text(el, 'Descriptor/CommonName') or text(el, 'Notes')
            lat = text(el, 'Place/Location/Latitude')
            lon = text(el, 'Place/Location/Longitude')
            if lat and lon:
                stops[prefix + code] = {
                    'id': prefix + code,
                    'name': name,
                    'lat': float(lat),
                    'lon': float(lon),
                    'suburb': text(el, 'Place/Suburb'),
                }
            el.clear()
        elif tag == 'Operator':
            oid = el.get('id') or text(el, 'OperatorCode')
            name = text(el, 'OperatorName') or text(el, 'TradingName')
            if oid:
                operators[oid] = name
            el.clear()
        elif tag == 'Service':
            code = text(el, 'ServiceCode')
            if not code:
                el.clear()
                continue
            lines = []
            for ln in el.iter():
                if local(ln.tag) == 'Line':
                    nm = text(ln, 'LineName')
                    if nm:
                        lines.append(nm)
            jps = {}
            for jp in el.iter():
                if local(jp.tag) != 'JourneyPattern':
                    continue
                jid = jp.get('id')
                refs = []
                for child in jp.iter():
                    if local(child.tag) == 'JourneyPatternSectionRefs' and child.text:
                        refs.append(child.text.strip())
                jps[jid] = refs
            start = text(el, 'OperatingPeriod/StartDate')
            end = text(el, 'OperatingPeriod/EndDate')
            services[code] = {
                'id': prefix + code,
                'agency': agency_id,
                'route_type': route_type,
                'line': (lines[0] if lines else code)[:60],
                'origin': text(el, 'StandardService/Origin'),
                'dest': text(el, 'StandardService/Destination'),
                'start': start.replace('-', ''),
                'end': end.replace('-', ''),
                'days': days_from(find_local(el, 'OperatingProfile')),
                'patterns': jps,
                'op': text(el, 'RegisteredOperatorRef'),
            }
            el.clear()
        elif tag == 'JourneyPatternSection':
            jid = el.get('id')
            links = []
            for lk in el:
                if local(lk.tag) != 'JourneyPatternTimingLink':
                    continue
                frm = to = None
                runtime = 0.0
                wait = 0.0
                for part in lk:
                    nm = local(part.tag)
                    if nm == 'From':
                        frm = text(part, 'StopPointRef')
                    elif nm == 'To':
                        to = text(part, 'StopPointRef')
                        wait = duration_min(text(part, 'WaitTime'))
                    elif nm == 'RunTime':
                        runtime = duration_min(part.text)
                if frm and to:
                    links.append((frm, to, runtime, wait))
            if jid:
                patterns[jid] = links
            el.clear()
        elif tag == 'VehicleJourney':
            code = text(el, 'VehicleJourneyCode')
            dep = clock(text(el, 'DepartureTime'))
            svc = text(el, 'ServiceRef')
            jp = text(el, 'JourneyPatternRef')
            if code and dep is not None and svc and jp:
                journeys.append({
                    'id': prefix + code,
                    'prefix': prefix,
                    'service': svc,
                    'pattern': jp,
                    'dep': dep,
                    'days': days_from(find_local(el, 'OperatingProfile')),
                })
            el.clear()
    return stops, services, patterns, journeys, operators


def write_gtfs(out, stops, services, patterns, journeys, agencies):
    os.makedirs(out, exist_ok=True)

    def dump(name, header, rows):
        with open(os.path.join(out, name), 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    dump('agency.txt',
         ['agency_id', 'agency_name', 'agency_url', 'agency_timezone'],
         [[a['id'], a['name'], a['url'], 'Europe/Dublin'] for a in agencies])

    dump('stops.txt',
         ['stop_id', 'stop_name', 'stop_lat', 'stop_lon', 'location_type'],
         [[s['id'], s['name'], '%.6f' % s['lat'], '%.6f' % s['lon'], 0]
          for s in stops.values()])

    route_rows, trip_rows, time_rows, cal_rows = [], [], [], []
    cal_seen = set()
    used_routes = set()

    for svc in services.values():
        cal_id = 'c_' + svc['id']
        if cal_id not in cal_seen:
            cal_seen.add(cal_id)
            start = svc['start'] or '20260101'
            end = svc['end'] or '20271231'
            cal_rows.append([cal_id] + svc['days'] + [start, end])

    for j in journeys:
        svc = services.get(j['service'])
        prefix = j.get('prefix', '')
        if not svc:
            continue
        links = []
        for ref in svc['patterns'].get(j['pattern'], []):
            links.extend(patterns.get(ref, []))
        if not links:
            continue
        # tiempos acumulados desde la salida
        seq = []
        t = 0.0
        seq.append((links[0][0], t))
        for frm, to, runtime, wait in links:
            t += runtime
            seq.append((to, t))
            t += wait
        if len(seq) < 2:
            continue
        route_id = 'r_' + svc['id']
        if route_id not in used_routes:
            used_routes.add(route_id)
            route_rows.append([route_id, svc['agency'],
                               svc['line'], (svc['origin'] + ' - ' + svc['dest'])[:120],
                               svc['route_type']])
        days = j['days'] or svc['days']
        cal_id = 'c_' + svc['id']
        if days != svc['days']:
            cal_id = cal_id + '_' + ''.join(str(d) for d in days)
            if cal_id not in cal_seen:
                cal_seen.add(cal_id)
                cal_rows.append([cal_id] + days + [svc['start'] or '20260101', svc['end'] or '20271231'])
        trip_rows.append([route_id, cal_id, j['id']])
        base = j['dep']
        for i, (stop, off) in enumerate(seq):
            stop_id = prefix + stop
            if stop_id not in stops_by_id:
                continue
            time_rows.append([j['id'], fmt(base + off), fmt(base + off), stop_id, i + 1])

    dump('routes.txt',
         ['route_id', 'agency_id', 'route_short_name', 'route_long_name', 'route_type'],
         route_rows)
    dump('calendar.txt',
         ['service_id', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday',
          'saturday', 'sunday', 'start_date', 'end_date'],
         cal_rows)
    dump('trips.txt', ['route_id', 'service_id', 'trip_id'], trip_rows)
    dump('stop_times.txt',
         ['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence'],
         time_rows)
    return len(stops), len(route_rows), len(trip_rows), len(time_rows)


stops_by_id = {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True, help='carpeta GTFS de salida')
    ap.add_argument('inputs', nargs='+', help='ZIP o XML: agency:route_type:fichero')
    args = ap.parse_args()

    all_stops, all_services, all_patterns, all_journeys = {}, {}, {}, []
    agencies = []
    tmpdir = os.path.join(args.out, '_txc')
    os.makedirs(tmpdir, exist_ok=True)

    for spec in args.inputs:
        parts = spec.split('|')
        agency_id, route_type, path = parts[0], parts[1], parts[2]
        label = parts[3] if len(parts) > 3 else agency_id
        url = parts[4] if len(parts) > 4 else 'https://www.translink.co.uk/'
        prefix = agency_id.upper() + '__'
        agencies.append({'id': agency_id, 'name': label, 'url': url, 'prefix': prefix})

        files = []
        if path.lower().endswith('.zip'):
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    if name.lower().endswith('.xml'):
                        z.extract(name, tmpdir)
                        files.append(os.path.join(tmpdir, name))
        else:
            files.append(path)

        for f in files:
            print('  leyendo %s (%s)' % (os.path.basename(f), agency_id), flush=True)
            st, sv, pa, jo, ops = read_transxchange(f, agency_id, route_type, prefix)
            all_stops.update(st)
            all_services.update(sv)
            all_patterns.update(pa)
            all_journeys.extend(jo)
            print('    %d paradas, %d servicios, %d patrones, %d viajes'
                  % (len(st), len(sv), len(pa), len(jo)), flush=True)

    stops_by_id.update(all_stops)
    n_stops, n_routes, n_trips, n_times = write_gtfs(
        args.out, all_stops, all_services, all_patterns, all_journeys, agencies)
    print('GTFS: %d paradas, %d rutas, %d viajes, %d horarios'
          % (n_stops, n_routes, n_trips, n_times))


if __name__ == '__main__':
    main()