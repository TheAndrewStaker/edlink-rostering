"""Infrastructure layer: database, messaging, scheduling, audit.

Concrete adapters for the services above. The Azure mocks live in
infrastructure/azure_mocks and expose the same interfaces as the real Azure
SDK classes so the swap to production is configuration, not refactor.
"""
