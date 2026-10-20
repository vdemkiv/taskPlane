"""The documented authoring route survives canonical regeneration."""
import json
import shutil
import subprocess
import sys
from pathlib import Path


def test_canonical_lens_authoring_roundtrip(tmp_path):
    root = Path(__file__).resolve().parents[2]
    shutil.copytree(root / 'scripts', tmp_path / 'scripts')
    shutil.copytree(root / 'lenses', tmp_path / 'lenses')
    (tmp_path / 'docs').mkdir()
    source = tmp_path / 'lenses/_catalog_data.json'
    rows = json.loads(source.read_text())
    added = {**rows[0], 'id': 'stabilization-fixture', 'name': 'Stabilization fixture'}
    rows.append(added)
    source.write_text(json.dumps(rows))
    guides = tmp_path / 'lenses/_guides_data.json'
    data = json.loads(guides.read_text())
    data[added['id']] = data[rows[0]['id']]
    guides.write_text(json.dumps(data))
    subprocess.run([sys.executable, str(tmp_path / 'lenses/_generate_catalog.py')], check=True)
    subprocess.run([sys.executable, str(tmp_path / 'lenses/_generate_lens_prompts.py')], check=True)
    subprocess.run([sys.executable, str(tmp_path / 'scripts/gen_lens_catalog.py')], check=True)
    catalog = json.loads((tmp_path / 'lenses/catalog.json').read_text())
    assert catalog['lenses']
    assert any(row['id'] == added['id'] for row in catalog['lenses'])
    guide = (tmp_path / 'docs/lens-catalog.md').read_text()
    assert '_catalog_data.json' in guide and '_guides_data.json' in guide
    for lens in catalog['lenses']:
        assert (tmp_path / 'lenses' / (lens['id'] + '.md')).is_file()
