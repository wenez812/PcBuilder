from bot.middlewares.throttling import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(max_actions=3, window=5.0, cooldown=10.0):
    clock = Clock()
    return RateLimiter(max_actions, window, cooldown, clock=clock), clock


def test_allows_up_to_limit():
    limiter, _ = make()
    assert [limiter.check(1) for _ in range(3)] == [(True, False)] * 3


def test_blocks_after_limit_and_warns_once():
    limiter, _ = make()
    for _ in range(3):
        limiter.check(1)
    assert limiter.check(1) == (False, True)   # первое превышение — предупреждаем
    assert limiter.check(1) == (False, False)  # дальше блокируем молча


def test_unblocks_after_cooldown():
    limiter, clock = make()
    for _ in range(4):
        limiter.check(1)
    clock.now += 11
    assert limiter.check(1) == (True, False)


def test_window_slides():
    limiter, clock = make()
    for _ in range(3):
        limiter.check(1)
    clock.now += 6  # окно прошло, события устарели
    assert limiter.check(1) == (True, False)


def test_users_are_independent():
    limiter, _ = make()
    for _ in range(4):
        limiter.check(1)
    assert limiter.check(2) == (True, False)


def test_cleanup_drops_stale_entries():
    limiter, clock = make()
    limiter.check(1)
    clock.now += 100
    limiter.cleanup()
    assert 1 not in limiter._events
