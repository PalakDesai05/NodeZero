import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
_d = None

def get_driver():
    global _d
    if _d is None:
        uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        _d = GraphDatabase.driver(uri, auth=(user, password))
    return _d

def close():
    global _d
    if _d is not None:
        try:
            _d.close()
        except Exception:
            pass
        _d = None

def run(q, **p):
    """Run a Cypher query; reconnects once if Neo4j Desktop dropped the session."""
    global _d
    for attempt in (0, 1):
        try:
            driver = get_driver()
            with driver.session() as s:
                return [r.data() for r in s.run(q, **p)]
        except (ServiceUnavailable, SessionExpired):
            close()
            if attempt:
                raise
