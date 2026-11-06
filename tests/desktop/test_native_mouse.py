from apps.deskpet.desktop.native_mouse import wait_for_release


def test_release_monitor_waits_until_left_button_is_up():
    states = iter([True, True, False])
    sleeps = []
    wait_for_release(lambda: next(states), sleeps.append)
    assert sleeps == [0.01, 0.01]


def test_release_before_monitor_starts_finishes_immediately():
    sleeps = []
    wait_for_release(lambda: False, sleeps.append)
    assert sleeps == []
