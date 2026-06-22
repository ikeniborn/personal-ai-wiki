from paw.api.errors import ProblemError, problem_response


def test_problem_response_shape():
    exc = ProblemError(status=409, title="Conflict", detail="stale revision")
    resp = problem_response(exc)
    assert resp.status_code == 409
    assert resp.media_type == "application/problem+json"


def test_problem_error_defaults():
    exc = ProblemError(status=404, title="Not Found")
    assert exc.detail is None
