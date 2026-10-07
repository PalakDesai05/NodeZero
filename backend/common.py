import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
_d = None

def run(q, **p):
    """Run a Cypher query; reconnects once if Neo4j Desktop dropped the session."""
    global _d
    for attempt in (0, 1):
        try:
            _d = _d or GraphDatabase.driver(
                os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687"),
                auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password")))
            with _d.session() as s:
                return [r.data() for r in s.run(q, **p)]
        except (ServiceUnavailable, SessionExpired):
            if _d: _d.close()
            _d = None
            if attempt: raise
