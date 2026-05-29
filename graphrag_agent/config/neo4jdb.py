"""Neo4j connection helper for the Fin* graph."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd
from langchain_neo4j import Neo4jGraph
from neo4j import GraphDatabase, Result

from graphrag_agent.config.settings import NEO4J_CONFIG


class DBConnectionManager:
    """Small singleton-style Neo4j manager used by graph ingestion scripts."""

    _instance: Optional["DBConnectionManager"] = None

    def __new__(cls) -> "DBConnectionManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self.neo4j_uri = NEO4J_CONFIG["uri"]
        self.neo4j_username = NEO4J_CONFIG["username"]
        self.neo4j_password = NEO4J_CONFIG["password"]
        self.max_pool_size = NEO4J_CONFIG["max_pool_size"]
        self.driver = GraphDatabase.driver(
            self.neo4j_uri,
            auth=(self.neo4j_username, self.neo4j_password),
            max_connection_pool_size=self.max_pool_size,
        )
        self.graph = Neo4jGraph(
            url=self.neo4j_uri,
            username=self.neo4j_username,
            password=self.neo4j_password,
            refresh_schema=NEO4J_CONFIG["refresh_schema"],
        )
        self.session_pool = []
        self._initialized = True

    def get_driver(self):
        return self.driver

    def get_graph(self):
        return self.graph

    def execute_query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        return self.driver.execute_query(
            cypher,
            parameters_=params or {},
            result_transformer_=Result.to_df,
        )

    def get_session(self):
        if self.session_pool:
            return self.session_pool.pop()
        return self.driver.session()

    def release_session(self, session) -> None:
        if len(self.session_pool) < self.max_pool_size:
            self.session_pool.append(session)
        else:
            session.close()

    def close(self) -> None:
        for session in self.session_pool:
            try:
                session.close()
            except Exception:
                pass
        self.session_pool = []
        if self.driver:
            self.driver.close()

    def __enter__(self) -> "DBConnectionManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


db_manager: Optional[DBConnectionManager] = None


def get_db_manager() -> DBConnectionManager:
    global db_manager
    if db_manager is None:
        db_manager = DBConnectionManager()
    return db_manager
