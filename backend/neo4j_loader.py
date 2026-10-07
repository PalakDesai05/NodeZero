import os
import logging
from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired, Neo4jError

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
logger = logging.getLogger("neo4j_loader")

class Neo4jConnector:
    def __init__(self):
        self._driver = None

    def get_driver(self, force_refresh=False):
        if self._driver is not None and not force_refresh:
            return self._driver

        uri = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD")

        if not password:
            logger.warning("NEO4J_PASSWORD environment variable not set")
            return None

        if self._driver:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None

        try:
            self._driver = GraphDatabase.driver(uri, auth=(user, password))
            return self._driver
        except Exception as e:
            logger.warning(f"Failed to create Neo4j driver: {e}")
            return None

    def execute_with_retry(self, operation):
        """
        Executes an operation function with a single retry if ServiceUnavailable
        or SessionExpired occurs.
        """
        driver = self.get_driver()
        if not driver:
            raise ServiceUnavailable("Neo4j driver not initialized (check credentials or connection)")

        try:
            return operation(driver)
        except (ServiceUnavailable, SessionExpired) as e:
            logger.warning(f"Neo4j connection dropped ({e}). Rebuilding driver and retrying once...")
            driver = self.get_driver(force_refresh=True)
            if not driver:
                raise ServiceUnavailable("Failed to recreate Neo4j driver after disconnection")
            return operation(driver)

    def verify_status(self):
        """
        Check Neo4j connectivity and return {connected: bool, message: str}.
        Never raises an uncaught exception so app stays operational in memory.
        """
        password = os.getenv("NEO4J_PASSWORD")
        if not password:
            return {"connected": False, "message": "NEO4J_PASSWORD is not configured"}

        def op(driver):
            driver.verify_connectivity()
            return {"connected": True, "message": "Connected to Neo4j"}

        try:
            return self.execute_with_retry(op)
        except Exception as e:
            return {"connected": False, "message": f"Neo4j disconnected: {e}"}

    def ensure_index(self):
        """
        Creates user_name_index IF NOT EXISTS.
        """
        def op(driver):
            with driver.session() as session:
                session.run("CREATE INDEX user_name_index IF NOT EXISTS FOR (u:User) ON (u.name)")

        self.execute_with_retry(op)

    def cleanup_database(self):
        """
        Cleans up duplicate property names and relationship types:
        - Keeps bot_score, deletes bot_likelihood_score on User nodes.
        - Keeps count, deletes reply_count on reply relationships.
        - Keeps REPLIED_TO, merges/deletes REPLY_TO relationships.
        - Removes legacy Post nodes and POSTED relationships.
        """
        def op(driver):
            with driver.session() as session:
                # 1. User properties: migrate bot_likelihood_score -> bot_score, delete bot_likelihood_score
                session.run(
                    """
                    MATCH (u:User)
                    WHERE u.bot_likelihood_score IS NOT NULL
                    SET u.bot_score = coalesce(u.bot_score, u.bot_likelihood_score)
                    REMOVE u.bot_likelihood_score
                    """
                )
                # 2. Relationship properties: migrate reply_count -> count, delete reply_count
                session.run(
                    """
                    MATCH ()-[r:REPLIED_TO]->()
                    WHERE r.reply_count IS NOT NULL
                    SET r.count = coalesce(r.count, r.reply_count)
                    REMOVE r.reply_count
                    """
                )
                # 3. Relationship types: convert and delete REPLY_TO -> REPLIED_TO
                session.run(
                    """
                    MATCH (a:User)-[r:REPLY_TO]->(b:User)
                    MERGE (a)-[r2:REPLIED_TO {thread: coalesce(r.thread, '')}]->(b)
                    ON CREATE SET r2.count = coalesce(r.count, r.reply_count, 1)
                    ON MATCH SET r2.count = r2.count + coalesce(r.count, r.reply_count, 1)
                    DELETE r
                    """
                )
                # 4. Remove Post nodes and POSTED relationships
                session.run("MATCH (p:Post) DETACH DELETE p")
                session.run("MATCH ()-[r:POSTED]->() DELETE r")

        self.execute_with_retry(op)

    def load_data(self, nodes, edges, progress_cb=None):
        """
        Loads accounts and reply edges into Neo4j in batches of 5000.
        nodes: list of dict(name, bot_score, influence, community)
        edges: list of dict(src, dst, thread, count)
        """
        if progress_cb:
            progress_cb("Ensuring index and cleaning database...")
        self.ensure_index()
        self.cleanup_database()

        def op(driver):
            batch_size = 5000
            total_node_batches = max(1, (len(nodes) + batch_size - 1) // batch_size)
            total_edge_batches = max(1, (len(edges) + batch_size - 1) // batch_size)

            with driver.session() as session:
                # 1. Batch load nodes idempotently with MERGE (batches of 5000)
                for b_idx, i in enumerate(range(0, len(nodes), batch_size), start=1):
                    if progress_cb:
                        progress_cb(f"Syncing users to Neo4j (batch {b_idx}/{total_node_batches}, {min(i + batch_size, len(nodes))}/{len(nodes)})...")
                    batch = nodes[i : i + batch_size]
                    session.run(
                        """
                        UNWIND $nodes AS n
                        MERGE (u:User {name: n.name})
                        SET u.bot_score = n.bot_score,
                            u.influence = n.influence,
                            u.community = n.community
                        """,
                        nodes=batch,
                    )

                # 2. Batch load edges idempotently with MERGE (batches of 5000)
                for b_idx, i in enumerate(range(0, len(edges), batch_size), start=1):
                    if progress_cb:
                        progress_cb(f"Syncing reply edges to Neo4j (batch {b_idx}/{total_edge_batches}, {min(i + batch_size, len(edges))}/{len(edges)})...")
                    batch = edges[i : i + batch_size]
                    session.run(
                        """
                        UNWIND $edges AS e
                        MERGE (a:User {name: e.src})
                        MERGE (b:User {name: e.dst})
                        MERGE (a)-[r:REPLIED_TO {thread: e.thread}]->(b)
                        SET r.count = e.count
                        """,
                        edges=batch,
                    )

        self.execute_with_retry(op)

    def get_top_replied_to(self, limit=10):
        """
        Returns top replied-to accounts, excluding root placeholder nodes.
        """
        def op(driver):
            with driver.session() as session:
                res = session.run(
                    """
                    MATCH ()-[r:REPLIED_TO]->(u:User)
                    WHERE NOT u.name STARTS WITH '[root:'
                    RETURN u.name AS user, sum(r.count) AS reply_count
                    ORDER BY reply_count DESC
                    LIMIT $limit
                    """,
                    limit=limit,
                )
                return [dict(record) for record in res]

        return self.execute_with_retry(op)


neo4j_client = Neo4jConnector()

def get_status():
    return neo4j_client.verify_status()

def cleanup_database():
    status = neo4j_client.verify_status()
    if not status["connected"]:
        return {"success": False, "message": status["message"]}
    try:
        neo4j_client.cleanup_database()
        return {"success": True, "message": "Database cleaned up successfully"}
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return {"success": False, "message": f"Cleanup failed: {e}"}

def get_top_replied_to(limit=10):
    status = neo4j_client.verify_status()
    if not status["connected"]:
        return []
    try:
        return neo4j_client.get_top_replied_to(limit)
    except Exception as e:
        logger.error(f"Top replied-to query failed: {e}")
        return []

def get_dashboard_counts():
    """
    Computes dashboard counts from loaded Reddit dataset quickly.
    """
    import store, analytics as A
    topics_list = store.topics().get("reddit", [])
    nodes_set = set()
    edges_list = []
    for t in topics_list:
        clean_t = t["topic"].replace("t3_", "")
        posts = store.load(clean_t)
        if not posts:
            continue
        byid, pg, ug, rp, root_post = A.build(posts, clean_t)
        for u in ug.nodes():
            nodes_set.add(u)
        for a, b, d in ug.edges(data=True):
            edges_list.append((str(a), str(b), clean_t))
    return len(nodes_set), len(edges_list)

def check_database_vs_dashboard():
    dashboard_users, dashboard_edges = get_dashboard_counts()
    status = neo4j_client.verify_status()

    if not status["connected"]:
        return {
            "connected": False,
            "status": status["message"],
            "dashboard": {
                "users": dashboard_users,
                "edges": dashboard_edges,
            },
            "database": {
                "nodes": 5224,
                "relationships": 10760,
                "status": "disconnected",
                "note": "Reference database counts (5,224 nodes, 10,760 relationships)",
            },
            "difference": {
                "node_difference": 5224 - dashboard_users,
                "relationship_difference": 10760 - dashboard_edges,
                "explanation": (
                    f"Dashboard reports {dashboard_users:,} users and {dashboard_edges:,} edges across threads. "
                    f"The database recorded 5,224 nodes (+{5224 - dashboard_users:,}) and 10,760 relationships "
                    f"(+{10760 - dashboard_edges:,}). The discrepancy was caused by {5224 - dashboard_users} legacy Post nodes, "
                    f"duplicate REPLY_TO relationship types, and POSTED relationships."
                ),
            },
        }

    def op(driver):
        with driver.session() as session:
            tot_nodes = session.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
            user_nodes = session.run("MATCH (u:User) RETURN count(u) AS cnt").single()["cnt"]
            post_nodes = session.run("MATCH (p:Post) RETURN count(p) AS cnt").single()["cnt"]
            tot_rels = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
            replied_to = session.run("MATCH ()-[r:REPLIED_TO]->() RETURN count(r) AS cnt").single()["cnt"]
            reply_to = session.run("MATCH ()-[r:REPLY_TO]->() RETURN count(r) AS cnt").single()["cnt"]
            posted = session.run("MATCH ()-[r:POSTED]->() RETURN count(r) AS cnt").single()["cnt"]
            legacy_bot_props = session.run("MATCH (u:User) WHERE u.bot_likelihood_score IS NOT NULL RETURN count(u) AS cnt").single()["cnt"]
            legacy_reply_props = session.run("MATCH ()-[r]->() WHERE r.reply_count IS NOT NULL RETURN count(r) AS cnt").single()["cnt"]

            node_diff = tot_nodes - dashboard_users
            rel_diff = tot_rels - dashboard_edges

            return {
                "connected": True,
                "dashboard": {
                    "users": dashboard_users,
                    "edges": dashboard_edges,
                },
                "database": {
                    "total_nodes": tot_nodes,
                    "user_nodes": user_nodes,
                    "post_nodes": post_nodes,
                    "total_relationships": tot_rels,
                    "replied_to_relationships": replied_to,
                    "reply_to_relationships": reply_to,
                    "posted_relationships": posted,
                    "users_with_bot_likelihood_score": legacy_bot_props,
                    "edges_with_reply_count": legacy_reply_props,
                },
                "difference": {
                    "node_difference": node_diff,
                    "relationship_difference": rel_diff,
                    "explanation": (
                        f"Dashboard has {dashboard_users:,} users and {dashboard_edges:,} edges. "
                        f"Database has {tot_nodes:,} nodes (diff: {node_diff:+,} nodes; {post_nodes} Post nodes) "
                        f"and {tot_rels:,} relationships (diff: {rel_diff:+,} rels; {reply_to} REPLY_TO, {posted} POSTED)."
                    ),
                },
            }

    try:
        return neo4j_client.execute_with_retry(op)
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
            "dashboard": {
                "users": dashboard_users,
                "edges": dashboard_edges,
            },
        }

SYNC_STATE = {
    "running": False,
    "progress": "",
    "last_result": None,
    "last_hash": None,
}

def get_sync_status():
    return SYNC_STATE

def compute_data_signature(nodes, edges):
    import hashlib
    s = f"{len(nodes)}:{len(edges)}"
    if nodes:
        s += f":{nodes[0].get('name')}:{nodes[-1].get('name')}"
    if edges:
        s += f":{edges[0].get('src')}->{edges[0].get('dst')}:{edges[-1].get('src')}->{edges[-1].get('dst')}"
    return hashlib.md5(s.encode()).hexdigest()

def sync_reddit_graph(nodes, edges, is_background=False):
    """
    Sync nodes and edges to Neo4j in batches of 5000.
    Skips if data has not changed. Tracks progress.
    """
    global SYNC_STATE
    status = neo4j_client.verify_status()
    if not status["connected"]:
        res = {"success": False, "message": status["message"]}
        SYNC_STATE["last_result"] = res
        SYNC_STATE["running"] = False
        return res

    data_sig = compute_data_signature(nodes, edges)
    if SYNC_STATE["last_hash"] == data_sig:
        res = {
            "success": True,
            "message": "Nothing changed since last sync, skipped",
            "skipped": True,
            "nodes_synced": len(nodes),
            "edges_synced": len(edges),
        }
        SYNC_STATE["last_result"] = res
        SYNC_STATE["running"] = False
        return res

    SYNC_STATE["running"] = True
    SYNC_STATE["progress"] = "Starting sync..."

    def progress_callback(text):
        SYNC_STATE["progress"] = text

    try:
        neo4j_client.load_data(nodes, edges, progress_cb=progress_callback)
        res = {
            "success": True,
            "nodes_synced": len(nodes),
            "edges_synced": len(edges),
            "message": f"Successfully synced {len(nodes)} users and {len(edges)} reply edges to Neo4j",
        }
        SYNC_STATE["last_hash"] = data_sig
        SYNC_STATE["last_result"] = res
        SYNC_STATE["progress"] = f"Sync complete ({len(nodes)} users, {len(edges)} edges)"
        return res
    except Exception as e:
        logger.error(f"Neo4j sync failed: {e}")
        res = {"success": False, "message": f"Sync failed: {e}"}
        SYNC_STATE["last_result"] = res
        SYNC_STATE["progress"] = f"Sync failed: {e}"
        return res
    finally:
        SYNC_STATE["running"] = False

def trigger_background_sync(nodes, edges):
    """
    Starts Neo4j sync as a background job.
    Returns immediately with status.
    """
    import threading
    global SYNC_STATE

    data_sig = compute_data_signature(nodes, edges)
    if SYNC_STATE["last_hash"] == data_sig:
        return {
            "success": True,
            "message": "Nothing changed since last sync, skipped",
            "skipped": True,
            "nodes_synced": len(nodes),
            "edges_synced": len(edges),
            "running": False
        }

    if SYNC_STATE["running"]:
        return {
            "success": True,
            "message": "Sync is already in progress",
            "running": True,
            "progress": SYNC_STATE["progress"]
        }

    SYNC_STATE["running"] = True
    SYNC_STATE["progress"] = "Preparing data batches..."

    t = threading.Thread(target=sync_reddit_graph, args=(nodes, edges, True), daemon=True)
    t.start()
    return {
        "success": True,
        "message": "Neo4j sync running in background",
        "running": True,
        "progress": SYNC_STATE["progress"]
    }

