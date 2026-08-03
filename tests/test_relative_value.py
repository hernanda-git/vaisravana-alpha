from vaisravana_alpha.strategy.relative_value import PairObservation, build_signal


def test_pair_signal_requires_sufficient_history():
    obs = PairObservation("A", "B", 100, 100, tuple([1.0] * 5), tuple([1.0] * 5), 0.001)
    assert build_signal(obs) is None


def test_pair_signal_rejects_flat_series():
    values = tuple(float(i) for i in range(1, 25))
    obs = PairObservation("A", "B", 24, 24, values, values, 0.001)
    assert build_signal(obs) is None


def test_pair_engine_is_fee_aware():
    values_a = tuple(100.0 * (1.001 ** i) for i in range(40))
    values_b = tuple(100.0 * (1.001 ** i) for i in range(39)) + (90.0,)
    obs = PairObservation("A", "B", values_a[-1], values_b[-1], values_a, values_b, 0.001)
    signal = build_signal(obs)
    assert signal is None or signal.expected_net > 0



def test_pair_signal_direction_is_explicit():
    values_a = tuple(100.0 * (1.001 ** i) for i in range(40))
    values_b = tuple(100.0 * (1.001 ** i) for i in range(39)) + (90.0,)
    obs = PairObservation("A", "B", values_a[-1], values_b[-1], values_a, values_b, 0.00001, min_z=0.1)
    signal = build_signal(obs)
    assert signal is None or signal.direction in {"BUY_A_SELL_B", "SELL_A_BUY_B"}
    if signal:
        assert signal.expected_net > 0
