"""Pick a good demo patient: has multiple MIMIC-IV-Note discharge summaries,
matching admissions in MIMIC.admissions, and at least one diagnosis on file."""
from pymongo import MongoClient

c = MongoClient('mongodb://localhost:27017', serverSelectionTimeoutMS=3000)
notes = c['MIMIC_Clinical_Notes']['discharge']
mimic = c['MIMIC']

pipe = [
    {'$group': {'_id': '$subject_id', 'n': {'$sum': 1}, 'hadm_ids': {'$addToSet': '$hadm_id'}}},
    {'$match': {'n': {'$gte': 2, '$lte': 6}}},
    {'$sort': {'n': -1}},
    {'$limit': 25},
]
candidates = []
for r in notes.aggregate(pipe):
    sid = r['_id']
    nc = r['n']
    hids = sorted(r['hadm_ids'])[:6]
    in_adm = mimic['admissions'].count_documents({'subject_id': sid})
    in_diag = mimic['diagnoses_icd'].count_documents({'subject_id': sid})
    if in_adm and in_diag:
        candidates.append((sid, nc, in_adm, in_diag, hids))

candidates.sort(key=lambda x: (-x[1], -x[3]))
print(f'{"subject_id":<12} {"notes":>5} {"adm":>4} {"diag":>5}  hadm_ids')
print('-' * 70)
for sid, nc, adm, dg, hids in candidates[:10]:
    print(f'{sid:<12} {nc:>5} {adm:>4} {dg:>5}  {hids}')

if candidates:
    sid, nc, adm, dg, hids = candidates[0]
    print()
    print(f'PICKED  subject_id={sid}  hadm_id={hids[0]}  ({nc} notes total)')
    sample = notes.find_one({'hadm_id': hids[0]}, {'_id': 0})
    if sample:
        print('  note_type:', sample.get('note_type'))
        print('  charttime:', sample.get('charttime'))
        print('  text first 200 chars:', repr((sample.get('text') or '')[:200]))
