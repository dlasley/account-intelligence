# src/synthetic — synthetic data generation package
# Dependency constraint: never import src.db from this package.
# Flow: generators produce payloads → orchestrator wraps as RawInboundEvent → caller
# feeds to process_event() → pipeline writes to DB.
