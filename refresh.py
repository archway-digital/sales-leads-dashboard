#!/usr/bin/env python3
"""
Fetch open leads from HubSpot and regenerate index.html from template.html.
Requires: HUBSPOT_TOKEN environment variable.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

TOKEN = os.environ.get('HUBSPOT_TOKEN')
if not TOKEN:
    sys.exit('Error: HUBSPOT_TOKEN environment variable not set.\n'
             'Run: export HUBSPOT_TOKEN=your_token_here')

BASE = 'https://api.hubapi.com'

# Owner ID → short key used in the dashboard
OWNERS = {
    '78945480': 'Sam',
    '54043254': 'Hannah',
    '44825479': 'Walter',
    '41320826': 'Brad',
}

# HubSpot lead status → dashboard stage label
STAGE_MAP = {
    'new':                    'New',
    'attempting_to_contact':  'Attempting',
    'in_progress':            'Attempting',
    'open':                   'Attempting',
    'connected':              'Connected',
    'discovery_booked':       'Discovery Booked',
    'open_deal':              'Discovery Booked',
    'qualified_to_buy':       'Discovery Booked',
}


def hs_post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={'Authorization': f'Bearer {TOKEN}',
                 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f'HTTP {e.code} on {path}: {e.read().decode()[:200]}')
        raise


def hs_get(path):
    req = urllib.request.Request(
        BASE + path,
        headers={'Authorization': f'Bearer {TOKEN}'})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ── 1. Fetch all leads for the four reps ──────────────────────────────────────
print('Fetching leads…')
all_leads = []
after = None
while True:
    body = {
        'filterGroups': [{'filters': [{
            'propertyName': 'hubspot_owner_id',
            'operator': 'IN',
            'values': list(OWNERS.keys()),
        }]}],
        'properties': ['hubspot_owner_id', 'hs_lead_status', 'createdate'],
        'limit': 200,
    }
    if after:
        body['after'] = after
    result = hs_post('/crm/v3/objects/0-136/search', body)
    all_leads.extend(result.get('results', []))
    after = result.get('paging', {}).get('next', {}).get('after')
    if not after:
        break

print(f'  {len(all_leads)} leads found')


# ── 2. Fetch lead → contact associations in batches of 100 ───────────────────
print('Fetching lead→contact associations…')
lead_to_contact = {}
lead_ids = [l['id'] for l in all_leads]

for i in range(0, len(lead_ids), 100):
    chunk = lead_ids[i:i + 100]
    try:
        resp = hs_post('/crm/v3/associations/0-136/0-1/batch/read',
                       {'inputs': [{'id': lid} for lid in chunk]})
        for item in resp.get('results', []):
            from_id = item.get('from', {}).get('id')
            tos = [t['id'] for t in item.get('to', [])]
            if from_id and tos:
                lead_to_contact[from_id] = tos[0]
    except Exception as e:
        print(f'  Association batch error (chunk {i}): {e}')

print(f'  {len(lead_to_contact)} leads have an associated contact')


# ── 3. Batch-read contact properties ─────────────────────────────────────────
print('Reading contact properties…')
contact_ids = list(set(lead_to_contact.values()))
contact_data = {}

CONTACT_PROPS = [
    'firstname', 'lastname', 'company',
    'notes_last_contacted', 'notes_next_activity_date',
    'hs_email_open', 'hs_sales_email_last_replied',
]

for i in range(0, len(contact_ids), 100):
    chunk = contact_ids[i:i + 100]
    try:
        resp = hs_post('/crm/v3/objects/contacts/batch/read', {
            'inputs': [{'id': cid} for cid in chunk],
            'properties': CONTACT_PROPS,
        })
        for c in resp.get('results', []):
            contact_data[c['id']] = c.get('properties', {})
    except Exception as e:
        print(f'  Contact batch error (chunk {i}): {e}')

print(f'  {len(contact_data)} contacts loaded')


# ── 4. Build raw array ────────────────────────────────────────────────────────
now = datetime.now(timezone.utc)
today_str = now.strftime('%Y-%m-%d')


def fmt_date(ts):
    if not ts:
        return None
    try:
        s = str(ts)
        if 'T' in s or s.endswith('Z'):
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d')
        return s[:10] if len(s) >= 10 else None
    except Exception:
        return None


def js_str(v):
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    escaped = str(v).replace('\\', '\\\\').replace("'", "\\'")
    return f"'{escaped}'"


# Count contact names to detect duplicates
contact_name_count = {}
for lead in all_leads:
    cid = lead_to_contact.get(lead['id'])
    cp = contact_data.get(cid, {}) if cid else {}
    fname = (cp.get('firstname') or '').strip()
    lname = (cp.get('lastname') or '').strip()
    name = (fname + ' ' + lname).strip() or f'Contact {cid or lead["id"]}'
    contact_name_count[name] = contact_name_count.get(name, 0) + 1

raw_entries = []
for lead in all_leads:
    props = lead.get('properties', {})
    owner_id = str(props.get('hubspot_owner_id') or '')
    owner = OWNERS.get(owner_id, 'Sam')

    raw_stage = (props.get('hs_lead_status') or 'new').lower().strip()
    stage = STAGE_MAP.get(raw_stage, 'New')

    hrs = 0
    create_ts = props.get('createdate')
    if create_ts:
        try:
            ct = datetime.fromisoformat(str(create_ts).replace('Z', '+00:00'))
            hrs = int((now - ct).total_seconds() / 3600)
        except Exception:
            hrs = 0

    cid = lead_to_contact.get(lead['id'])
    cp = contact_data.get(cid, {}) if cid else {}

    fname = (cp.get('firstname') or '').strip()
    lname = (cp.get('lastname') or '').strip()
    contact = (fname + ' ' + lname).strip() or f'Contact {cid or lead["id"]}'
    company = (cp.get('company') or '').strip()

    # Prefer hs_sales_email_last_replied, fall back to notes_last_contacted
    reply_date = fmt_date(cp.get('hs_sales_email_last_replied') or
                          cp.get('notes_last_contacted'))
    next_date = fmt_date(cp.get('notes_next_activity_date'))

    try:
        opens = int(cp.get('hs_email_open') or 0)
    except (ValueError, TypeError):
        opens = 0

    dup = contact_name_count.get(contact, 0) > 1

    raw_entries.append({
        'id': lead['id'],
        'contact': contact,
        'company': company,
        'stage': stage,
        'owner': owner,
        'hrs': hrs,
        'reply': reply_date,
        'next': next_date,
        'opens': opens,
        'dup': dup,
        'flag': None,
    })

# Sort oldest-first to match original ordering
raw_entries.sort(key=lambda r: r['hrs'], reverse=True)
print(f'  Built {len(raw_entries)} raw entries')


# ── 5. Generate JS data block ─────────────────────────────────────────────────
lines = []
for r in raw_entries:
    lines.append(
        f"  {{id:{js_str(r['id'])},contact:{js_str(r['contact'])},"
        f"company:{js_str(r['company'])},stage:{js_str(r['stage'])},"
        f"owner:{js_str(r['owner'])},hrs:{r['hrs']},"
        f"reply:{js_str(r['reply'])},next:{js_str(r['next'])},"
        f"opens:{r['opens']},dup:{js_str(r['dup'])},flag:{js_str(r['flag'])}}}"
    )

data_block = (
    f"const TODAY = new Date('{today_str}');\n"
    f"const MS_DAY = 86400000;\n"
    f"\n"
    f"const raw = [\n"
    + ',\n'.join(lines) + ',\n'
    + '];\n'
)

# ── 6. Inject into template and write index.html ─────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(script_dir, 'template.html')
output_path = os.path.join(script_dir, 'index.html')

with open(template_path, 'r') as f:
    template = f.read()

if '// {{LEAD_DATA}}' not in template:
    sys.exit('Error: placeholder "// {{LEAD_DATA}}" not found in template.html')

# Also update the subtitle date
months = ['January','February','March','April','May','June',
          'July','August','September','October','November','December']
date_label = f"{months[now.month-1]} {now.day}, {now.year}"

output = template.replace('// {{LEAD_DATA}}', data_block)
output = output.replace('{{DATE_LABEL}}', date_label)

with open(output_path, 'w') as f:
    f.write(output)

print(f'\nDone! index.html updated — {len(raw_entries)} leads as of {date_label}')
