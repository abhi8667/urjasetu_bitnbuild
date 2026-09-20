"""Verify real simulation recording with deterministic provider substitutes."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from engine.agents.ai_trading import AITradingStrategyAgent, LLMResponse
from engine.algo import llm
from engine.config import DEFAULT
from server.simulation import build_simulation


class StrategyStreamTests(unittest.TestCase):
    def test_ml_risk_reaches_ai_and_ui(self):
        contexts = []
        def provider(model, context, timeout):
            contexts.append(context)
            return '{"discount":0.81,"margin":0.13}'
        with patch.object(AITradingStrategyAgent, '_groq_request', side_effect=provider), patch.object(llm, '_call_llm') as legacy:
            run = build_simulation(config=replace(DEFAULT, llm_enabled=True), days=1)
        legacy.assert_not_called()
        self.assertEqual(len(contexts), 1)
        self.assertEqual(len(contexts[0]['grid_risk']), 4)
        risk = [e for e in run.events if e['kind'] == 'grid_risk_predicted']
        self.assertEqual(len(risk), 96)
        self.assertTrue(all(e['agent'] == 'grid_risk' for e in risk))
        decisions = [e for e in run.events if e['kind'] == 'ai_strategy_updated']
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]['agent'], 'ai_trading')
        self.assertEqual(decisions[0]['block'], 0)
        self.assertIn('discount 0.81', decisions[0]['text'])
        self.assertIn('margin 0.13', decisions[0]['text'])
        self.assertEqual(sum('reused for this block' in e['text'] for e in run.events), 23)

    def test_disabled_ai_keeps_ml_running_without_provider_calls(self):
        with patch.object(AITradingStrategyAgent, '_groq_request') as provider:
            run = build_simulation(config=replace(DEFAULT, llm_enabled=False), days=1)
        provider.assert_not_called()
        self.assertEqual(sum(e['agent'] == 'grid_risk' for e in run.events), 96)
        self.assertTrue(all('disabled' in e['text'] for e in run.events if e['agent'] == 'ai_trading'))

    def test_reasoning_is_emitted_before_the_final_strategy(self):
        response = LLMResponse(
            content='{"discount":0.81,"margin":0.13}',
            reasoning='High grid risk means bids should be less aggressive.',
        )
        with patch.object(AITradingStrategyAgent, '_groq_request', return_value=response):
            run = build_simulation(config=replace(DEFAULT, llm_enabled=True), days=1)
        ai_events = [e for e in run.events if e['block'] == 0 and e['agent'] == 'ai_trading']
        self.assertEqual(ai_events[0]['kind'], 'ai_strategy_thinking')
        self.assertEqual(ai_events[1]['kind'], 'ai_strategy_updated')
        self.assertIn('High grid risk', ai_events[0]['text'])

    def test_provider_failure_is_visible_and_simulation_finishes(self):
        with patch.object(AITradingStrategyAgent, '_groq_request', side_effect=TimeoutError('private details')) as provider:
            run = build_simulation(config=replace(DEFAULT, llm_enabled=True), days=1)
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(len(run.blocks), 24)
        failures = [e for e in run.events if e['agent'] == 'ai_trading']
        self.assertTrue(all('previous strategy retained' in e['text'] for e in failures))
        self.assertFalse(any('private details' in e['text'] for e in failures))


if __name__ == '__main__':
    unittest.main()
