# OPTIONAL: copy a data CSV into Neo4j (pip install neo4j). Usage: python neo4j_export.py data/reddit.csv
# Env: NEO4J_PASSWORD (required), NEO4J_URI (default neo4j://127.0.0.1:7687), NEO4J_USER (default neo4j)
import os, sys, pandas as pd
from neo4j import GraphDatabase
df = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else 'data/sample.csv', dtype=str).fillna('')
df['parent_id'] = df.parent_id.str.replace(r'^t\d_', '', regex=True)
who, edges = dict(zip(df.id, df.author)), {}
for r in df.itertuples():
    b = who.get(r.parent_id)
    if b and b != r.author: edges[(r.author, b)] = edges.get((r.author, b), 0) + 1
drv = GraphDatabase.driver(os.getenv('NEO4J_URI', 'neo4j://127.0.0.1:7687'), auth=(os.getenv('NEO4J_USER', 'neo4j'), os.environ['NEO4J_PASSWORD']))
with drv.session() as s:
    s.run('CREATE INDEX user_name_index IF NOT EXISTS FOR (u:User) ON (u.name)')
    s.run('UNWIND $rows AS r MERGE (x:User {name: r.a}) MERGE (y:User {name: r.b}) MERGE (x)-[e:REPLIED_TO]->(y) SET e.count = r.n',
          rows=[{'a': a, 'b': b, 'n': n} for (a, b), n in edges.items()])
print(f'loaded {len(edges)} reply edges into Neo4j')
