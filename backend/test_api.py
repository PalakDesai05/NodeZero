#!/usr/bin/env python3
import os
import sys

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_endpoints():
    print("Testing FastAPI Endpoints...")

    # 0. Test root and health endpoints
    r_root = client.get('/')
    assert r_root.status_code == 200, f"root failed: {r_root.status_code}"
    r_health = client.get('/health')
    assert r_health.status_code == 200, f"health failed: {r_health.status_code}"
    print("[OK] GET / and GET /health returned 200")

    # 1. Test topics
    r = client.get('/topics')
    assert r.status_code == 200, f"topics failed: {r.status_code}"
    data = r.json()
    assert len(data['reddit']) == 5, f"expected 5 reddit topics, got {len(data['reddit'])}"
    print("[OK] GET /topics returned 5 threads")

    # 2. Test analysis for b7yijr (AutoModerator was first comment)
    r = client.get('/analysis/b7yijr')
    assert r.status_code == 200, f"analysis failed: {r.status_code}"
    res = r.json()
    assert res['summary']['root']['author'] == 'voteronly10', f"expected voteronly10, got {res['summary']['root']['author']}"
    assert res['summary']['root']['post_type'] == 'Earliest comment', "expected Earliest comment"
    print(f"[OK] GET /analysis/b7yijr root is {res['summary']['root']['author']} ({res['summary']['root']['post_type']}, {res['summary']['root']['label']})")

    # 3. Test analysis limit parameter
    r_limit = client.get('/analysis/b7sa1t?limit=300')
    count_limit = len(r_limit.json()['nodes'])
    assert count_limit <= 301, f"expected <= 301, got {count_limit}"
    
    r_all = client.get('/analysis/b7sa1t?limit=0')
    count_all = len(r_all.json()['nodes'])
    assert count_all > 300, f"expected all nodes (> 300), got {count_all}"
    print(f"[OK] GET /analysis/b7sa1t limit=300 -> {count_limit} nodes, limit=0 -> {count_all} nodes")

    # 4. Test neo4j check
    r_check = client.get('/neo4j/check')
    assert r_check.status_code == 200, f"neo4j/check failed: {r_check.status_code}"
    check_data = r_check.json()
    assert check_data['dashboard']['users'] == 4702
    assert check_data['dashboard']['edges'] == 6800
    assert 'difference' in check_data
    print(f"[OK] GET /neo4j/check: Dashboard users={check_data['dashboard']['users']}, edges={check_data['dashboard']['edges']}")
    print(f"     Difference details: {check_data['difference']}")

    # 5. Test neo4j top-replied
    r_top = client.get('/neo4j/top-replied')
    assert r_top.status_code == 200
    print("[OK] GET /neo4j/top-replied passed")

    # 6. Test neo4j status
    r_status = client.get('/neo4j/status')
    assert r_status.status_code == 200
    assert "connected" in r_status.json()
    print(f"[OK] GET /neo4j/status connected={r_status.json().get('connected')}")

    print("\nALL FASTAPI ENDPOINT TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_endpoints()
