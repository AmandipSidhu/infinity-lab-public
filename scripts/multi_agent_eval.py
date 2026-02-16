#!/usr/bin/env python3
"""
Multi-Agent Evaluation System
Based on arXiv:2409.06289 framework for LLM-based quantitative trading

Evaluates strategies across multiple dimensions before execution:
- Market fit analysis
- Risk profile assessment
- Backtestability check
- Auto-reject weak strategies before compute
"""

import json
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class EvaluationScore(Enum):
    """Strategy evaluation scores."""
    REJECT = 0
    WEAK = 1
    ACCEPTABLE = 2
    GOOD = 3
    EXCELLENT = 4


@dataclass
class EvaluationResult:
    """Result of multi-agent evaluation."""
    market_fit: EvaluationScore
    risk_profile: EvaluationScore
    backtestability: EvaluationScore
    overall_score: float
    recommendation: str
    reasoning: Dict[str, str]
    
    def should_proceed(self) -> bool:
        """Check if strategy should proceed to implementation."""
        # Reject if any dimension scores REJECT or if overall < 2.0
        if (self.market_fit == EvaluationScore.REJECT or
            self.risk_profile == EvaluationScore.REJECT or
            self.backtestability == EvaluationScore.REJECT):
            return False
        
        return self.overall_score >= 2.0


class MarketFitAgent:
    """
    Evaluates if strategy aligns with current market conditions.
    Checks: trend vs range-bound, volatility regime, liquidity requirements
    """
    
    def evaluate(self, strategy_spec: str) -> Tuple[EvaluationScore, str]:
        """Evaluate market fit of strategy."""
        score = EvaluationScore.ACCEPTABLE
        reasoning = []
        
        # Check for market regime awareness
        if any(term in strategy_spec.lower() for term in ['trend', 'momentum', 'breakout']):
            if 'range' in strategy_spec.lower() or 'sideways' in strategy_spec.lower():
                reasoning.append("❌ Conflicting signals: trend strategy in range-bound context")
                score = EvaluationScore.WEAK
            else:
                reasoning.append("✅ Momentum-based strategy - works in trending markets")
                score = EvaluationScore.GOOD
        
        elif any(term in strategy_spec.lower() for term in ['mean reversion', 'oversold', 'overbought']):
            if 'trend' in strategy_spec.lower():
                reasoning.append("⚠️ Mean reversion in trending market - high risk")
                score = EvaluationScore.WEAK
            else:
                reasoning.append("✅ Mean reversion strategy - works in range-bound markets")
                score = EvaluationScore.GOOD
        
        # Check for volatility awareness
        if any(term in strategy_spec.lower() for term in ['volatility', 'vix', 'atr']):
            reasoning.append("✅ Volatility-aware strategy")
            if score.value < EvaluationScore.GOOD.value:
                score = EvaluationScore.GOOD
        
        # Check for dangerous patterns
        if 'buy and hold' in strategy_spec.lower():
            reasoning.append("⚠️ Buy-and-hold: no risk management mentioned")
            score = EvaluationScore.WEAK
        
        if 'grid trading' in strategy_spec.lower() or 'martingale' in strategy_spec.lower():
            reasoning.append("❌ High-risk pattern: grid/martingale strategies")
            score = EvaluationScore.REJECT
        
        # Default reasonable score if no red flags
        if not reasoning:
            reasoning.append("✅ Basic strategy - acceptable market fit")
            score = EvaluationScore.ACCEPTABLE
        
        return score, "\n".join(reasoning)


class RiskProfileAgent:
    """
    Evaluates risk management approach.
    Checks: position sizing, stop losses, drawdown limits, leverage
    """
    
    # Leverage thresholds
    MAX_SAFE_LEVERAGE = 5  # Maximum safe leverage multiplier
    MAX_LEVERAGE_CHECK = 21  # Upper bound for leverage detection
    
    def evaluate(self, strategy_spec: str) -> Tuple[EvaluationScore, str]:
        """Evaluate risk profile of strategy."""
        score = EvaluationScore.ACCEPTABLE
        reasoning = []
        risk_elements = 0
        
        # Check for position sizing
        if any(term in strategy_spec.lower() for term in ['position size', 'kelly', 'risk per trade']):
            reasoning.append("✅ Position sizing defined")
            risk_elements += 1
            score = EvaluationScore.GOOD
        
        # Check for stop loss
        if any(term in strategy_spec.lower() for term in ['stop loss', 'stop-loss', 'max loss']):
            reasoning.append("✅ Stop loss mechanism present")
            risk_elements += 1
        else:
            reasoning.append("⚠️ No stop loss mentioned - add protective stops")
            score = EvaluationScore.WEAK
        
        # Check for drawdown management
        if any(term in strategy_spec.lower() for term in ['drawdown', 'max dd', 'risk limit']):
            reasoning.append("✅ Drawdown awareness")
            risk_elements += 1
        
        # Check for leverage warnings
        if any(term in strategy_spec.lower() for term in ['leverage', 'margin', '2x', '3x']):
            if ('high leverage' in strategy_spec.lower() or 
                any(f'{i}x' in strategy_spec.lower() for i in range(self.MAX_SAFE_LEVERAGE, self.MAX_LEVERAGE_CHECK))):
                reasoning.append(f"❌ Excessive leverage detected (>{self.MAX_SAFE_LEVERAGE}x)")
                score = EvaluationScore.REJECT
            else:
                reasoning.append("⚠️ Leverage mentioned - ensure risk controls")
                if score.value > EvaluationScore.ACCEPTABLE.value:
                    score = EvaluationScore.ACCEPTABLE
        
        # Check for diversification
        if any(term in strategy_spec.lower() for term in ['diversif', 'multiple', 'basket', 'portfolio']):
            reasoning.append("✅ Diversification considered")
            risk_elements += 1
        
        # Reward comprehensive risk management
        if risk_elements >= 3:
            score = EvaluationScore.EXCELLENT
            reasoning.append("🌟 Comprehensive risk management")
        elif risk_elements == 0:
            reasoning.append("❌ No risk management - reject")
            score = EvaluationScore.REJECT
        
        return score, "\n".join(reasoning)


class BacktestabilityAgent:
    """
    Evaluates if strategy can be properly backtested.
    Checks: data requirements, computational feasibility, parameter clarity
    """
    
    def evaluate(self, strategy_spec: str) -> Tuple[EvaluationScore, str]:
        """Evaluate backtestability of strategy."""
        score = EvaluationScore.ACCEPTABLE
        reasoning = []
        
        # Check for clear entry/exit rules
        if any(term in strategy_spec.lower() for term in ['entry', 'buy when', 'enter', 'signal']):
            reasoning.append("✅ Entry rules defined")
        else:
            reasoning.append("⚠️ Entry rules unclear")
            score = EvaluationScore.WEAK
        
        if any(term in strategy_spec.lower() for term in ['exit', 'sell when', 'close', 'take profit']):
            reasoning.append("✅ Exit rules defined")
        else:
            reasoning.append("⚠️ Exit rules unclear")
            score = EvaluationScore.WEAK
        
        # Check for data requirements
        exotic_indicators = ['ichimoku', 'elliott wave', 'fibonacci retracement', 'gann']
        if any(indicator in strategy_spec.lower() for indicator in exotic_indicators):
            reasoning.append("⚠️ Complex indicators - may be hard to backtest")
            if score.value > EvaluationScore.ACCEPTABLE.value:
                score = EvaluationScore.ACCEPTABLE
        
        # Check for forward-looking bias
        if any(term in strategy_spec.lower() for term in ['future', 'predict', 'forecast']):
            reasoning.append("⚠️ Potential look-ahead bias - ensure causality")
        
        # Check for reasonable timeframe
        if any(term in strategy_spec.lower() for term in ['minute', 'second', 'tick']):
            reasoning.append("⚠️ High-frequency strategy - may have execution risk")
        
        # Check for parameter clarity
        if re.search(r'\d+\s*(day|period|window|ma|ema|sma)', strategy_spec.lower()):
            reasoning.append("✅ Parameters specified (e.g., 20-day MA)")
            score = EvaluationScore.GOOD
        
        # Check for reasonable symbols
        if any(term in strategy_spec.lower() for term in ['spy', 'qqq', 'iwm', 'dia', 'equities', 'etf']):
            reasoning.append("✅ Liquid instruments specified")
        elif any(term in strategy_spec.lower() for term in ['penny stock', 'otc', 'illiquid']):
            reasoning.append("❌ Illiquid instruments - reject")
            score = EvaluationScore.REJECT
        
        if not reasoning:
            reasoning.append("✅ Basic backtest requirements met")
        
        return score, "\n".join(reasoning)


class MultiAgentEvaluator:
    """
    Coordinates all evaluation agents.
    Provides final recommendation on strategy quality.
    """
    
    def __init__(self):
        self.market_fit_agent = MarketFitAgent()
        self.risk_profile_agent = RiskProfileAgent()
        self.backtestability_agent = BacktestabilityAgent()
    
    def evaluate(self, strategy_spec: str) -> EvaluationResult:
        """Run full multi-agent evaluation."""
        print("="*60)
        print("Multi-Agent Strategy Evaluation")
        print("="*60 + "\n")
        
        # Run each agent
        print("🔍 Agent 1: Market Fit Analysis")
        market_fit, market_reasoning = self.market_fit_agent.evaluate(strategy_spec)
        print(f"Score: {market_fit.name}")
        print(market_reasoning + "\n")
        
        print("🔍 Agent 2: Risk Profile Assessment")
        risk_profile, risk_reasoning = self.risk_profile_agent.evaluate(strategy_spec)
        print(f"Score: {risk_profile.name}")
        print(risk_reasoning + "\n")
        
        print("🔍 Agent 3: Backtestability Check")
        backtestability, backtest_reasoning = self.backtestability_agent.evaluate(strategy_spec)
        print(f"Score: {backtestability.name}")
        print(backtest_reasoning + "\n")
        
        # Calculate overall score (weighted average)
        overall_score = (
            market_fit.value * 0.3 +
            risk_profile.value * 0.4 +  # Risk is most important
            backtestability.value * 0.3
        )
        
        # Generate recommendation
        if overall_score >= 3.0:
            recommendation = "✅ PROCEED - Excellent strategy"
        elif overall_score >= 2.5:
            recommendation = "✅ PROCEED - Good strategy"
        elif overall_score >= 2.0:
            recommendation = "⚠️ PROCEED WITH CAUTION - Acceptable strategy"
        elif overall_score >= 1.0:
            recommendation = "⚠️ WEAK - Consider revisions"
        else:
            recommendation = "❌ REJECT - Strategy not viable"
        
        result = EvaluationResult(
            market_fit=market_fit,
            risk_profile=risk_profile,
            backtestability=backtestability,
            overall_score=overall_score,
            recommendation=recommendation,
            reasoning={
                "market_fit": market_reasoning,
                "risk_profile": risk_reasoning,
                "backtestability": backtest_reasoning
            }
        )
        
        print("="*60)
        print(f"Overall Score: {overall_score:.2f}/4.0")
        print(f"Recommendation: {recommendation}")
        print("="*60 + "\n")
        
        return result


def main():
    """Example usage of multi-agent evaluator."""
    evaluator = MultiAgentEvaluator()
    
    # Test case 1: Good strategy
    print("\n📊 Test Case 1: RSI Mean Reversion Strategy\n")
    good_strategy = """
    Create an RSI mean reversion strategy for SPY:
    - Buy when RSI(14) < 30 (oversold)
    - Sell when RSI(14) > 70 (overbought)
    - Position size: 2% of portfolio per trade
    - Stop loss: 3% below entry
    - Max drawdown limit: 20%
    - Works best in range-bound markets
    """
    result1 = evaluator.evaluate(good_strategy)
    print(f"Should proceed: {result1.should_proceed()}\n")
    
    # Test case 2: Weak strategy (no risk management)
    print("\n📊 Test Case 2: Simple Moving Average Strategy\n")
    weak_strategy = """
    Create a moving average crossover strategy:
    - Buy when 50-day MA crosses above 200-day MA
    - Sell when 50-day MA crosses below 200-day MA
    """
    result2 = evaluator.evaluate(weak_strategy)
    print(f"Should proceed: {result2.should_proceed()}\n")
    
    # Test case 3: Rejected strategy (high risk)
    print("\n📊 Test Case 3: Grid Trading Strategy\n")
    rejected_strategy = """
    Create a grid trading strategy with 10x leverage on Bitcoin
    """
    result3 = evaluator.evaluate(rejected_strategy)
    print(f"Should proceed: {result3.should_proceed()}\n")


if __name__ == "__main__":
    main()
