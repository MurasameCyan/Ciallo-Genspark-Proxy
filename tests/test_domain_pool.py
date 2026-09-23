from app.domain_pool import DomainPool, parse_domains


def test_parse_domains_normalizes_and_deduplicates():
    assert parse_domains(" @one.example.com, two.example.com\n@one.example.com ") == [
        "one.example.com",
        "two.example.com",
    ]


def test_domain_pool_round_robin_and_fallback():
    pool = DomainPool.from_config({"mail_domains": ["a.example", "b.example"]})
    assert [pool.next_domain() for _ in range(3)] == [
        "a.example",
        "b.example",
        "a.example",
    ]
    assert DomainPool.from_config({}).next_domain("fallback.example") == "fallback.example"


def test_domain_pool_reload_preserves_next_item_when_config_changes():
    pool = DomainPool.from_config({"mail_domains": ["a.example", "b.example"]})
    assert pool.next_domain() == "a.example"
    pool.update_config({"mail_domains": ["b.example", "c.example"]})
    assert pool.next_domain() == "b.example"


def test_domain_pool_random_mode_uses_injected_rng():
    class Rng:
        def choice(self, values):
            return values[-1]

    pool = DomainPool.from_config(
        {"mail_domains": ["a.example", "b.example"], "mail_domain_mode": "random"},
        rng=Rng(),
    )
    assert pool.next_domain() == "b.example"
