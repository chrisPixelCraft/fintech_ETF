"""v2 C: second-round fixed research candidates, formal submission blocked.

Default: historical PIT pool. --track official_ex_post: retrospective official
150-name pool with explicit membership lookahead. Neither is an unseen test.
"""
from src.v2_second_best import make_config,run_fixed,single_cli

STRATEGY = 'C'
SCENARIO_SELECTIONS = {'historical_pit': 'p024', 'official_ex_post': 'p033'}
PARAMETERS_BY_TRACK = {'historical_pit': {'target_count': 25,
                    'replacement_margin': 0.1,
                    'max_replacements_per_day': 2,
                    'volatility_spike_ratio': 2.0,
                    'one_day_chase_return': 0.07,
                    'volume_low': 0.5,
                    'volume_high': 3.0,
                    'four_hour_mode': 'strict',
                    'returns': 'base',
                    'ema': 'base',
                    'macd': 'base',
                    'score_profile': 'slow',
                    'sector_top_fraction': 0.5,
                    'sector_short_weight': 0.5333333333333333,
                    'sector_fallback_mode': 'baseline',
                    'c_alpha': 0.08},
 'official_ex_post': {'target_count': 25,
                      'replacement_margin': 0.2,
                      'max_replacements_per_day': 2,
                      'volatility_spike_ratio': 2.5,
                      'one_day_chase_return': 0.095,
                      'volume_low': 0.3,
                      'volume_high': 5.0,
                      'four_hour_mode': 'strict',
                      'returns': 'base',
                      'ema': 'fast',
                      'macd': 'slow',
                      'score_profile': 'slow',
                      'sector_top_fraction': 0.5,
                      'sector_short_weight': 0.5333333333333333,
                      'sector_fallback_mode': 'baseline',
                      'c_alpha': 0.0}}
CANDIDATE_ID = SCENARIO_SELECTIONS.get('historical_pit')
PARAMETERS = PARAMETERS_BY_TRACK.get('historical_pit')


def build_config(track='historical_pit'):
    return make_config(STRATEGY,SCENARIO_SELECTIONS,PARAMETERS_BY_TRACK,track)


def run_backtest(context=None,track='historical_pit'):
    return run_fixed(STRATEGY,build_config(track),context)


def main(argv=None):
    return single_cli(STRATEGY,SCENARIO_SELECTIONS,PARAMETERS_BY_TRACK,argv)


if __name__=='__main__':main()
