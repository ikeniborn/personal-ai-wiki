from paw.obs import metrics


def test_render_metrics_exposes_names():
    metrics.HTTP_REQUESTS.labels(method="GET", route="/health", status="200").inc()
    metrics.LLM_COST.labels(op="ingest", model="gpt-4o-mini").inc(0.01)
    payload, content_type = metrics.render_metrics()
    text = payload.decode()
    assert "text/plain" in content_type
    assert "paw_http_requests_total" in text
    assert "paw_llm_cost_usd_total" in text
