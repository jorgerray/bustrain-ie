#!/usr/bin/env python3
"""
Genera BusData.html y TrainData.html para Bus&Train Irlanda.

Entrada : GTFS de Transport for Ireland (paquete nacional, todos los operadores).
Salida  : dos ficheros <script> con el modelo compacto comprimido en base64,
          el mismo formato que consume AppJs.html.

Modelo:
  C  paradas / localidades (lista de nombres)
  K  calendarios [dias(L-D 7), inicio, fin, "fecha:1,.."]
  P  patrones: [[idxParada, minutoOffset], ...]
  S  servicios: [idxPatron, minutosSalida, idxCalendario, linea, idxOperador]
  O  operadores (solo bus)

Uso:
  python3 build_data.py --gtfs /ruta/gtfs --out ../bus&train-apps-script-ie
"""
import argparse
import base64
import collections
import csv
import gzip
import json
import math
import re
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------- utilidades

def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as fh:
        yield from csv.DictReader(fh)


def hhmmss(t):
    if not t:
        return -1
    p = t.strip().split(':')
    if len(p) < 2:
        return -1
    try:
        return int(p[0]) * 3600 + int(p[1]) * 60 + (int(p[2]) if len(p) > 2 else 0)
    except ValueError:
        return -1


def ymd(s):
    return (s or '').strip()[:8]


# ---------------------------------------------------------------- localidades

NI_PLACES = [
    ('Belfast', 54.5964, -5.9301, 345000),
    ('Derry', 54.9966, -7.3086, 85000),
    ('Newry', 54.1751, -6.3402, 27000),
    ('Lisburn', 54.5234, -6.0353, 45000),
    ('Portadown', 54.4230, -6.4440, 22000),
    ('Lurgan', 54.4630, -6.3330, 25000),
]

AIRPORTS = [
    ('Dublin Airport', 53.4213, -6.2701),
    ('Cork Airport', 51.8413, -8.4911),
    ('Shannon Airport', 52.7020, -8.9248),
    ('Ireland West Airport Knock', 53.9103, -8.8185),
    ('Kerry Airport', 52.1809, -9.5238),
    ('Belfast International Airport', 54.6575, -6.2158),
    ('George Best Belfast City Airport', 54.6181, -5.8725),
]

STREET_WORDS = {'road', 'rd', 'street', 'st', 'hill', 'avenue', 'ave', 'park', 'bridge',
                'terrace', 'lane', 'close', 'court', 'green', 'view', 'drive', 'way',
                'place', 'square', 'quay', 'row', 'grove', 'heights', 'woods', 'crescent'}

# Suburbios que se funden con su ciudad: si el lugar está dentro del radio y no
# tiene estación de tren propia, la parada se etiqueta con la ciudad.
MAIN_CITIES = [
    ('Dublin', 53.3498, -6.2603, 15.0),
    ('Cork', 51.8985, -8.4756, 13.0),
    ('Limerick', 52.6638, -8.6267, 8.0),
    ('Galway', 53.2740, -9.0510, 8.0),
    ('Waterford', 52.2592, -7.1101, 8.0),
]

# Localidades dentro del área metropolitana que conservan nombre propio.
IRISH_TO_EN = {'An Muileann gCearr': 'Mullingar', 'An Ros': 'Ros'}

KEEP_SEPARATE = {'Dún Laoghaire', 'Malahide', 'Howth', 'Swords', 'Bray', 'Greystones',
                 'Cobh', 'Carrigaline', 'Tramore', 'Midleton', 'Passage West'}

# Nombres irlandeses que conviene mostrar en inglés.
NAME_FIX = {
    'Trá Mhór': 'Tramore',
    'Baile Átha Cliath': 'Dublin',
    'Corcaigh': 'Cork',
    'Gaillimh': 'Galway',
    'Luimneach': 'Limerick',
    'Port Láirge': 'Waterford',
    'Cill Chainnigh': 'Kilkenny',
    'Loch Garman': 'Wexford',
    'Droichead Átha': 'Drogheda',
    'Dún Dealgan': 'Dundalk',
    'Bré': 'Bray',
    'Sligeach': 'Sligo',
    'Trá Lí': 'Tralee',
    'Cill Airne': 'Killarney',
    'Inis': 'Ennis',
    'Áth Luain': 'Athlone',
    'An Uaimh': 'Navan',
    'Port Laoise': 'Portlaoise',
    'Cill Dara': 'Kildare',
}


class Gazetteer:
    def __init__(self, geonames_path):
        self.places = []          # (nombre, lat, lon, poblacion)
        if os.path.exists(geonames_path):
            for line in open(geonames_path, encoding='utf-8'):
                f = line.rstrip('\n').split('\t')
                if len(f) < 15 or f[6] != 'P' or f[7] == 'PPLX':
                    continue
                try:
                    lat, lon = float(f[4]), float(f[5])
                except ValueError:
                    continue
                pop = int(f[14]) if f[14].isdigit() else 0
                self.places.append((f[1], lat, lon, pop))
        for n, la, lo, p in NI_PLACES:
            self.places.append((n, la, lo, p))
        self.names = {p[0] for p in self.places}

    @staticmethod
    def km(lat1, lon1, lat2, lon2):
        return 111.0 * math.hypot(lat2 - lat1, (lon2 - lon1) * math.cos(math.radians(lat1)))

    def nearest(self, lat, lon, min_pop=1000, max_km=15.0):
        best, bd = None, 1e9
        for name, la, lo, pop in self.places:
            if pop < min_pop:
                continue
            d = self.km(lat, lon, la, lo)
            if d < bd:
                best, bd = name, d
        return (best, bd) if best and bd <= max_km else (None, None)

    def nearest_any(self, lat, lon, max_km=12.0):
        best, bd = None, 1e9
        for name, la, lo, pop in self.places:
            d = self.km(lat, lon, la, lo)
            if d < bd:
                best, bd = name, d
        return (best, bd) if best and bd <= max_km else (None, None)

    def local_for_stop(self, name, lat, lon, rail_places=()):
        """Localidad de una parada de autobús."""
        n = (name or '').strip()
        low = n.lower()
        # Aeropuertos: destino propio, muy usado en bus interurbano.
        for ap, la, lo in AIRPORTS:
            if 'airport' in low and self.km(lat, lon, la, lo) < 6:
                return ap
        # "Cork Bus Stn", "Galway Coach Station", "Sligo Busáras"...
        first = n.split()[0] if n.split() else ''
        if first and first in self.names and len(n.split()) > 1:
            second = n.split()[1].lower().strip(',')
            if second not in STREET_WORDS and second not in {'road', 'street'}:
                if second in {'bus', 'busáras', 'busaras', 'coach', 'station', 'stn', 'st'} or n[len(first):].strip().startswith(','):
                    return self._fold(NAME_FIX.get(first, first), lat, lon)
        name_loc, _ = self.nearest(lat, lon, min_pop=1000, max_km=15)
        if not name_loc:
            name_loc, _ = self.nearest_any(lat, lon, max_km=12)
        if not name_loc:
            return n
        return self._fold(NAME_FIX.get(name_loc, name_loc), lat, lon)

    def _fold(self, name_loc, lat, lon):
        name_loc = IRISH_TO_EN.get(name_loc, name_loc)
        """Suburbio sin identidad propia: se queda con el nombre de la ciudad."""
        if name_loc not in KEEP_SEPARATE:
            for city, cla, clo, radius in MAIN_CITIES:
                if name_loc != city and self.km(lat, lon, cla, clo) <= radius:
                    return city
        return name_loc


# ---------------------------------------------------------------- calendarios

class Calendars:
    def __init__(self, gtfs):
        self.by_service = {}
        path = os.path.join(gtfs, 'calendar.txt')
        if os.path.exists(path):
            for r in read_csv(path):
                days = ''.join('1' if r.get(d) == '1' else '0'
                               for d in ('monday', 'tuesday', 'wednesday', 'thursday',
                                         'friday', 'saturday', 'sunday'))
                self.by_service[r['service_id']] = [days, ymd(r['start_date']), ymd(r['end_date']), '']
        exc = collections.defaultdict(dict)
        path = os.path.join(gtfs, 'calendar_dates.txt')
        if os.path.exists(path):
            for r in read_csv(path):
                sid, date = r.get('service_id'), ymd(r.get('date'))
                if not sid or not date:
                    continue
                exc[sid][date] = 0 if r.get('exception_type') == '2' else 1
        for sid, days in exc.items():
            cal = self.by_service.get(sid)
            if cal is None:
                cal = ['1111111', min(days), max(days), '']
                self.by_service[sid] = cal
            cal[3] = ','.join('%s:%d' % (d, days[d]) for d in sorted(days))
        self.index = {}
        self.list = []

    def idx(self, sid):
        cal = self.by_service.get(sid)
        if cal is None:
            cal = ['1111111', '20000101', '20991231', '']
        key = '|'.join(cal)
        if key not in self.index:
            self.index[key] = len(self.list)
            self.list.append(cal)
        return self.index[key]


# ---------------------------------------------------------------- tren

DUBLIN = (53.3498, -6.2603)

RAIL_STATION_FIX = {
    'Cork (Kent)': 'Cork Kent',
    'Galway (Ceannt)': 'Galway Ceannt',
    'Limerick (Colbert)': 'Limerick Colbert',
    'Waterford (Plunkett)': 'Waterford Plunkett',
    'Sligo (MacDiarmada)': 'Sligo MacDiarmada',
    'Kilkenny (MacDonagh)': 'Kilkenny MacDonagh',
    "Wexford (O Hanrahan)": "Wexford O'Hanrahan",
    'Drogheda (MacBride)': 'Drogheda MacBride',
    'Dundalk (Clarke)': 'Dundalk Clarke',
    'Bray (Daly)': 'Bray Daly',
    'Dun Laoghaire (Mallin)': 'Dún Laoghaire Mallin',
    'Muine Bheag (Bagenalstown)': 'Muine Bheag',
    'Leixlip(Confey)': 'Leixlip Confey',
    'Leixlip(Louisa Bridge)': 'Leixlip Louisa Bridge',
    'Belfast': 'Belfast Grand Central',
}


BELFAST = (54.5964, -5.9301)


def clean_station(name, lat, lon, is_luas=False, gaz=None):
    n = RAIL_STATION_FIX.get(name, name)
    # Translink nombra las paradas "X Train Station": fuera el sufijo.
    n = re.sub(r'\s+(Train|Rail|Bus)\s+Station$', '', n).strip()
    n = re.sub(r'\s+Station$', '', n).strip()
    n = n.replace('Grand Central', 'Grand Central')
    if not is_luas and gaz is not None and not n.startswith('Belfast'):
        loc, _ = gaz.nearest(lat, lon, min_pop=4000, max_km=12)
        if loc == 'Belfast':
            return 'Belfast ' + n
    if is_luas:
        if not n.lower().startswith('dublin'):
            n = 'Dublin ' + n
        return n
    if 'Dublin' in n:
        return n
    if Gazetteer.km(lat, lon, *DUBLIN) < 16:
        return 'Dublin ' + n
    return n


def build_train(gtfs, gaz, label_by_agency):
    routes = {}
    for r in read_csv(os.path.join(gtfs, 'routes.txt')):
        if r.get('route_type') in ('2', '0'):
            routes[r['route_id']] = r
    if not routes:
        return []

    stops = {}
    for s in read_csv(os.path.join(gtfs, 'stops.txt')):
        if s['stop_id'] in set():
            pass
        stops[s['stop_id']] = s

    trips = {}
    for t in read_csv(os.path.join(gtfs, 'trips.txt')):
        r = routes.get(t['route_id'])
        if not r:
            continue
        trips[t['trip_id']] = {
            'route': r,
            'svc': t['service_id'],
            'head': t.get('trip_headsign', ''),
            'short': t.get('trip_short_name', ''),
            'stops': [],
        }

    for st in read_csv(os.path.join(gtfs, 'stop_times.txt')):
        t = trips.get(st['trip_id'])
        if not t:
            continue
        arr = hhmmss(st.get('arrival_time'))
        dep = hhmmss(st.get('departure_time'))
        if arr < 0:
            arr = dep
        if dep < 0:
            dep = arr
        if arr < 0:
            continue
        t['stops'].append((int(st.get('stop_sequence') or 0), st['stop_id'], arr, dep))

    cals = Calendars(gtfs)
    by_agency = collections.defaultdict(lambda: {'C': [], 'G': [], 'cidx': {}, 'P': [], 'pidx': {}, 'S': []})
    rail_stops = set()

    for t in trips.values():
        stops_t = sorted(t['stops'])
        if len(stops_t) < 2:
            continue
        route = t['route']
        aid = route['agency_id']
        model = by_agency[aid]
        for _seq, _sid, _arr, _dep in stops_t:
            rail_stops.add(_sid)
        is_luas = route.get('route_type') == '0'
        base = stops_t[0][2]
        pat = []
        for seq, sid, arr, dep in stops_t:
            s = stops.get(sid)
            if not s:
                continue
            try:
                lat, lon = float(s['stop_lat']), float(s['stop_lon'])
            except (KeyError, ValueError):
                lat = lon = 0.0
            name = clean_station(s['stop_name'], lat, lon, is_luas, gaz)
            if name not in model['cidx']:
                model['cidx'][name] = len(model['C'])
                model['C'].append(name)
                model['G'].append([round(lat, 5), round(lon, 5)])
            key = (model['cidx'][name], round((arr - base) / 60))
            if pat and pat[-1][0] == key[0]:
                continue
            pat.append(key)
        if len(pat) < 2:
            continue
        pkey = tuple(pat)
        if pkey not in model['pidx']:
            model['pidx'][pkey] = len(model['P'])
            model['P'].append([list(x) for x in pat])
        line = route.get('route_short_name') or ''
        if line.lower() in ('rail', '', 'ir rail'):
            line = route.get('route_long_name') or line
        model['S'].append([model['pidx'][pkey], round(base / 60), cals.idx(t['svc']), line, 0])

    out = []
    for aid, m in by_agency.items():
        if not m['S']:
            continue
        label, op, link = label_by_agency.get(aid, (aid, 'custom', ''))
        out.append({
            'meta': {'src': label, 'built': '', 'horizon': ''},
            'O': [label],
            'C': m['C'],
            'G': m['G'],
            'K': cals.list,
            'P': m['P'],
            'S': m['S'],
            'op': op,
            'label': label,
            'link': link,
        })
    return out, rail_stops


# ---------------------------------------------------------------- autobús

BUS_OPERATORS = [
    # (prefijo de agency_id o nombre, etiqueta, enlace)
    ('2', 'Bus Éireann', 'https://www.buseireann.ie/'),
    ('WFRD', 'Bus Éireann', 'https://www.buseireann.ie/'),
    ('1', 'Dublin Bus', 'https://www.dublinbus.ie/'),
    ('03C', 'Go-Ahead Ireland', 'https://www.goaheadireland.ie/'),
    ('3', 'Go-Ahead Ireland', 'https://www.goaheadireland.ie/'),
]


def bus_operator(aid, name):
    if aid == 'metro' or 'Metro' in name and 'Glider' in name:
        return 'Metro & Glider', 'https://www.translink.co.uk/'
    if aid == 'ulster' or 'Ulsterbus' in name:
        return 'Ulsterbus & Goldliner', 'https://www.translink.co.uk/'
    if aid in ('2', 'WFRD') or name.startswith('Bus Éireann'):
        return 'Bus Éireann', 'https://www.buseireann.ie/'
    if aid == '1' or 'Dublin Bus' in name:
        return 'Dublin Bus', 'https://www.dublinbus.ie/'
    if 'Go-Ahead' in name:
        return 'Go-Ahead Ireland', 'https://www.goaheadireland.ie/'
    if 'Local Link' in name:
        return 'TFI Local Link', 'https://www.transportforireland.ie/tfi-local-link/'
    clean = name.replace(' Limited', '').replace(' Ltd.', '').replace(' Ltd', '').strip()
    return clean, ''


def build_bus(gtfs, gaz, rail_stop_ids=()):
    routes = {}
    for r in read_csv(os.path.join(gtfs, 'routes.txt')):
        if r.get('route_type') == '3':
            routes[r['route_id']] = r
    if not routes:
        return None

    agencies = {a['agency_id']: a['agency_name'] for a in read_csv(os.path.join(gtfs, 'agency.txt'))}

    stops = {}
    for s in read_csv(os.path.join(gtfs, 'stops.txt')):
        stops[s['stop_id']] = s

    rail_places = set()
    for sid in rail_stop_ids:
        s = stops.get(sid)
        if not s:
            continue
        try:
            lat, lon = float(s['stop_lat']), float(s['stop_lon'])
        except (KeyError, ValueError):
            continue
        pl, _ = gaz.nearest(lat, lon, min_pop=1000, max_km=15)
        if pl:
            rail_places.add(NAME_FIX.get(pl, pl))
    print('localidades con estación de tren:', len(rail_places))

    loc_cache = {}
    def local(sid):
        if sid in loc_cache:
            return loc_cache[sid]
        s = stops.get(sid)
        if not s:
            loc_cache[sid] = None
            return None
        try:
            lat, lon = float(s['stop_lat']), float(s['stop_lon'])
        except (KeyError, ValueError):
            loc_cache[sid] = None
            return None
        loc_cache[sid] = gaz.local_for_stop(s['stop_name'], lat, lon, rail_places)
        return loc_cache[sid]

    trips = {}
    for t in read_csv(os.path.join(gtfs, 'trips.txt')):
        r = routes.get(t['route_id'])
        if not r:
            continue
        trips[t['trip_id']] = {'route': r, 'svc': t['service_id'], 'stops': []}

    for st in read_csv(os.path.join(gtfs, 'stop_times.txt')):
        t = trips.get(st['trip_id'])
        if not t:
            continue
        arr = hhmmss(st.get('arrival_time'))
        dep = hhmmss(st.get('departure_time'))
        if arr < 0:
            arr = dep
        if dep < 0:
            dep = arr
        if arr < 0:
            continue
        t['stops'].append((int(st.get('stop_sequence') or 0), st['stop_id'], arr))

    cals = Calendars(gtfs)
    C, cidx = [], {}
    P, pidx = [], {}
    S = []
    O, oidx = [], {}
    counts = collections.Counter()

    for t in trips.values():
        ss = sorted(t['stops'])
        if len(ss) < 2:
            continue
        seq = []
        for order, sid, arr in ss:
            l = local(sid)
            if not l:
                continue
            if seq and seq[-1][0] == l:
                continue
            seq.append([l, arr])
        if len(seq) < 2:
            continue
        if len({x[0] for x in seq}) < 2:
            continue   # viaje urbano: no aporta trayectos entre localidades
        base = seq[0][1]
        pat = []
        for l, arr in seq:
            if l not in cidx:
                cidx[l] = len(C)
                C.append(l)
            pat.append([cidx[l], round((arr - base) / 60)])
        pkey = tuple((a, b) for a, b in pat)
        if pkey not in pidx:
            pidx[pkey] = len(P)
            P.append(pat)
        aid = t['route']['agency_id']
        op, _link = bus_operator(aid, agencies.get(aid, aid))
        if op not in oidx:
            oidx[op] = len(O)
            O.append(op)
        line = t['route'].get('route_short_name') or t['route'].get('route_long_name') or ''
        S.append([pidx[pkey], round(base / 60), cals.idx(t['svc']), line, oidx[op]])
        counts[op] += 1

    return {'meta': {'src': 'TFI', 'built': ''}, 'O': O, 'C': C, 'K': cals.list, 'P': P, 'S': S}, counts


# ---------------------------------------------------------------- salida

def write_data(path, var, payload, comment):
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    gz = gzip.compress(raw, 9)
    b64 = base64.b64encode(gz).decode('ascii')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('<script>\n/* %s */\nwindow.%s="%s";\n</script>\n' % (comment, var, b64))
    return len(raw), len(gz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gtfs', default='/tmp/yukie/work/gtfs/all')
    ap.add_argument('--geonames', default='/tmp/yukie/work/IE.txt')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(__file__), '..', 'bus&train-apps-script-ie'))
    ap.add_argument('--busfeed', default='', help='carpeta donde escribir bus.json.gz (paquete remoto)')
    args = ap.parse_args()

    gaz = Gazetteer(args.geonames)
    built = datetime.now().strftime('%Y-%m-%d')
    horizon = ''
    fi = os.path.join(args.gtfs, 'feed_info.txt')
    if os.path.exists(fi):
        row = next(read_csv(fi))
        horizon = ymd(row.get('feed_end_date')) or ''
        built = '%s-%s-%s' % (ymd(row.get('feed_start_date'))[:4], ymd(row.get('feed_start_date'))[4:6], ymd(row.get('feed_start_date'))[6:8]) if row.get('feed_start_date') else built

    labels = {
        'IR': ('Irish Rail', 'irishrail', 'https://www.irishrail.ie/'),
        '10000': ('Luas', 'luas', 'https://www.luas.ie/'),
        'nir': ('NI Railways', 'nir', 'https://www.translink.co.uk/'),
    }
    trains, rail_stop_ids = build_train(args.gtfs, gaz, labels)
    for m in trains:
        m['meta']['built'] = built
        m['meta']['horizon'] = horizon
    train_payload = {'built': built, 'models': trains}

    bus = build_bus(args.gtfs, gaz, rail_stop_ids)
    bus_payload, counts = bus
    bus_payload['meta']['built'] = built

    os.makedirs(args.out, exist_ok=True)
    r1, g1 = write_data(os.path.join(args.out, 'BusData.html'), 'BUS_GZ', bus_payload,
                        'Bus&Train · Irish bus network (TFI GTFS, compressed).')
    r2, g2 = write_data(os.path.join(args.out, 'TrainData.html'), 'TRAIN_GZ', train_payload,
                        'Bus&Train · embedded trains (Irish Rail, Luas; compressed).')

    if args.busfeed:
        os.makedirs(args.busfeed, exist_ok=True)
        raw = json.dumps(bus_payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        gz = gzip.compress(raw, 9)
        dest = os.path.join(args.busfeed, 'bus.json.gz')
        with open(dest, 'wb') as fh:
            fh.write(gz)
        print('remoto: %s (%.2f MB gz, built %s)' % (dest, len(gz) / 1e6, bus_payload['meta']['built']))

    print('bus  : %d servicios, %d patrones, %d localidades, %d operadores'
          % (len(bus_payload['S']), len(bus_payload['P']), len(bus_payload['C']), len(bus_payload['O'])))
    print('       json %.1f MB -> gz %.2f MB' % (r1 / 1e6, g1 / 1e6))
    print('train: ' + ', '.join('%s %d servicios' % (m['label'], len(m['S'])) for m in trains))
    print('       json %.1f MB -> gz %.2f MB' % (r2 / 1e6, g2 / 1e6))
    print('operadores bus:', ', '.join('%s (%d)' % (k, v) for k, v in counts.most_common()))


if __name__ == '__main__':
    main()