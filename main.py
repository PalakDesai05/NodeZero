from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from neo4j import GraphDatabase

app = FastAPI(title="NodeZero API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

URI = "neo4j://127.0.0.1:7687"
USERNAME = "neo4j"
PASSWORD = "nodezero123"

driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD), connection_timeout=10)


@app.get("/")
def root():
    return {"message": "NodeZero API is running"}


@app.get("/top-influencers")
def top_influencers(limit: int = 10):
    query = """
        MATCH (u:User)<-[:REPLIED_TO]-()
        RETURN u.name AS user, count(*) AS replies_received
        ORDER BY replies_received DESC
        LIMIT $limit
    """
    with driver.session() as session:
        result = session.run(query, limit=limit)
        return [dict(record) for record in result]


@app.get("/top-bot-scores")
def top_bot_scores(limit: int = 10):
    query = """
        MATCH (u:User)
        WHERE u.bot_likelihood_score IS NOT NULL
        RETURN u.name AS user, u.bot_likelihood_score AS bot_score,
               u.reply_count AS reply_count, u.out_in_ratio AS out_in_ratio
        ORDER BY bot_score DESC
        LIMIT $limit
    """
    with driver.session() as session:
        result = session.run(query, limit=limit)
        return [dict(record) for record in result]


@app.get("/network-graph")
def network_graph(limit: int = 15):
    top_query = """
        MATCH (u:User)<-[:REPLIED_TO]-()
        RETURN u.name AS user, count(*) AS replies_received
        ORDER BY replies_received DESC
        LIMIT $limit
    """
    with driver.session() as session:
        top_users = [record["user"] for record in session.run(top_query, limit=limit)]

        edge_query = """
            MATCH (a:User)-[:REPLIED_TO]->(b:User)
            WHERE b.name IN $top_users
            RETURN a.name AS source, b.name AS target
            LIMIT 200
        """
        edges_result = session.run(edge_query, top_users=top_users)
        edges = [{"source": r["source"], "target": r["target"]} for r in edges_result]

        node_names = set(top_users)
        for e in edges:
            node_names.add(e["source"])
            node_names.add(e["target"])

        nodes = [{"id": name} for name in node_names]

    return {"nodes": nodes, "edges": edges}


@app.get("/trace-root/{comment_author}")
def trace_root_by_author(comment_author: str, max_depth: int = 50):
    chain = [comment_author]
    current = comment_author

    with driver.session() as session:
        for _ in range(max_depth):
            result = session.run(
                """
                MATCH (u:User {name: $name})-[:REPLIED_TO]->(next:User)
                RETURN next.name AS next_name
                LIMIT 1
                """,
                name=current,
                timeout=5
            )
            record = result.single()
            if record is None:
                break
            next_name = record["next_name"]
            if next_name in chain:
                break
            chain.append(next_name)
            current = next_name

    return {
        "author": comment_author,
        "found": len(chain) > 1,
        "root_author": chain[-1],
        "chain": chain,
        "chain_length": len(chain)
    }

@app.get("/timeline-data")
def timeline_data(limit: int = 300):
    query = """
        MATCH (a:User)-[r:REPLIED_TO]->(b:User)
        RETURN a.name AS source, b.name AS target
        LIMIT $limit
    """
    with driver.session() as session:
        result = session.run(query, limit=limit)
        edges = [{"source": r["source"], "target": r["target"]} for r in result]
    return {"edges": edges}
@app.get("/user/{username}/network")
def user_network(username: str):
    query = """
        MATCH (u:User {name: $username})-[:REPLIED_TO]-(connected)
        RETURN connected.name AS connected_user
    """
    with driver.session() as session:
        result = session.run(query, username=username)
        return [dict(record) for record in result]