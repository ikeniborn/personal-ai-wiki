LOGIN = "user.login"
LOGOUT = "user.logout"
SETUP_COMPLETE = "setup.complete"
USER_CREATE = "user.create"
USER_ROLE_CHANGE = "user.role_change"
USER_DELETE = "user.delete"
API_KEY_ISSUE = "api_key.issue"
API_KEY_REVOKE = "api_key.revoke"
PROVIDER_CHANGE = "provider.change"
INGEST_START = "ingest.start"
INGEST_ROLLBACK = "ingest.rollback"
# NOTE: no ingest/source delete operation exists in the services today; add an
# INGEST_DELETE constant and wire it when such a delete operation is introduced.
