"""
BacktestReporter — вся отчётность бэктеста.

Инкапсулирует: equity curve, monthly stats, professional metrics.
Отделён от торговой логики — TradingEngine ничего не знает о репортинге.
"""
import logging
from typing import List, Dict
from datetime import datetime

import numpy as np
import pandas as pd

from trading_engine.core.domain.models import TradeResult
from trading_engine.core.domain.portfolio import Portfolio
from trading_engine.core.domain.enums import Side

logger = logging.getLogger(__name__)


class BacktestReporter:
    """Сбор статистики и вывод отчётов бэктеста."""

    def __init__(self, initial_balance: float = 100.0):
        self.initial_balance = initial_balance
        self.equity_curve: List[float] = []
        self.equity_timestamps: List[datetime] = []
        self.monthly_stats: Dict[str, dict] = {}
        self.trades: List[TradeResult] = []

    def record_equity(self, balance: float, timestamp: datetime) -> None:
        """Записать точку equity curve."""
        self.equity_curve.append(balance)
        self.equity_timestamps.append(timestamp)

    def record_trade(self, trade: TradeResult, timestamp: datetime) -> None:
        """Записать сделку + обновить monthly stats."""
        self.trades.append(trade)

        month_key = timestamp.strftime("%Y-%m")
        if month_key not in self.monthly_stats:
            balance_now = self.equity_curve[-1] if self.equity_curve else self.initial_balance
            self.monthly_stats[month_key] = {
                "pnl_abs": 0.0, "trades": 0, "wins": 0, "start_balance": balance_now,
            }

        self.monthly_stats[month_key]["pnl_abs"] += trade.pnl_abs
        self.monthly_stats[month_key]["trades"] += 1
        if trade.net_pnl_pct > 0:
            self.monthly_stats[month_key]["wins"] += 1

    def print_summary(self, portfolio: Portfolio) -> None:
        """Вывести полный отчёт — как MVP bt.py lines 267-352."""
        print("\n" + "=" * 50)
        print("ИТОГОВЫЕ РЕЗУЛЬТАТЫ ПО ВСЕМ МОНЕТАМ")
        print("=" * 50)
        print(f"{'Месяц':<10} | {'Сделок':<8} | {'WinRate':<8} | {'Прибыль':<10}")
        print("-" * 50)

        total_pnl_abs = 0
        total_trades = 0
        total_wins = 0

        for m in sorted(self.monthly_stats.keys()):
            s = self.monthly_stats[m]
            count = s["trades"]
            wins = s["wins"]
            pnl_abs = s["pnl_abs"]
            start_bal = s["start_balance"]
            pnl_pct = (pnl_abs / start_bal * 100) if start_bal > 0 else 0
            wr = (wins / count * 100) if count > 0 else 0
            total_pnl_abs += pnl_abs
            total_trades += count
            total_wins += wins
            print(f"{m:<10} | {count:<8} | {wr:<7.1f}% | {pnl_pct:+.2f}% ({pnl_abs:+.2f}$)")

        print("-" * 50)
        final_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
        total_return = (portfolio.balance - self.initial_balance) / self.initial_balance * 100
        print(
            f"ИТОГО      | {total_trades:<8} | {final_wr:.1f}%     | "
            f"{total_return:+.2f}% ({total_pnl_abs:+.2f}$)"
        )
        print(f"\nКонечный баланс: {portfolio.balance:.2f}")
        print(f"Макс. просадка:  {portfolio.max_drawdown:.2f}%")

        total_longs = sum(1 for t in self.trades if t.side == Side.LONG)
        total_shorts = sum(1 for t in self.trades if t.side == Side.SHORT)
        longs_won = sum(1 for t in self.trades if t.side == Side.LONG and t.net_pnl_pct > 0)
        longs_lost = sum(1 for t in self.trades if t.side == Side.LONG and t.net_pnl_pct <= 0)
        shorts_won = sum(1 for t in self.trades if t.side == Side.SHORT and t.net_pnl_pct > 0)
        shorts_lost = sum(1 for t in self.trades if t.side == Side.SHORT and t.net_pnl_pct <= 0)

        print("\n" + "=" * 50)
        print("СТАТИСТИКА ПО НАПРАВЛЕНИЯМ")
        print("=" * 50)
        print(f"LONG  Всего: {total_longs:<4} | В плюс: {longs_won:<4} | В минус: {longs_lost:<4}")
        print(f"SHORT Всего: {total_shorts:<4} | В плюс: {shorts_won:<4} | В минус: {shorts_lost:<4}")
        print("-" * 50)

        self._print_metrics(portfolio)

    def _print_metrics(self, portfolio: Portfolio) -> None:
        """Professional metrics — Sharpe, Sortino, Calmar, PF."""
        if not self.equity_curve:
            return

        eq = pd.Series(self.equity_curve, index=self.equity_timestamps)
        daily = eq.resample("D").last().ffill()
        rets = daily.pct_change().dropna()

        if len(rets) <= 1 or rets.std() == 0:
            return

        days = (daily.index[-1] - daily.index[0]).days
        cagr = (daily.iloc[-1] / daily.iloc[0]) ** (365 / days) - 1 if days > 0 else 0
        sharpe = (rets.mean() / rets.std()) * np.sqrt(365)

        down = rets[rets < 0]
        sortino = (rets.mean() / down.std() * np.sqrt(365)) if len(down) > 1 and down.std() > 0 else 0
        calmar = cagr / (portfolio.max_drawdown / 100) if portfolio.max_drawdown > 0 else 0

        if self.trades:
            pnls = [t.pnl_abs for t in self.trades]
            gross_profit = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p < 0))
            pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        else:
            pf = 0

        print("\n" + "=" * 40)
        print("📊 ПРОФЕССИОНАЛЬНЫЕ МЕТРИКИ")
        print("=" * 40)
        print(f"Profit Factor:   {pf:.2f}")
        print(f"Sharpe Ratio:    {sharpe:.2f}")
        print(f"Sortino Ratio:   {sortino:.2f}")
        print(f"Calmar Ratio:    {calmar:.2f}")
        print(f"CAGR (Annual):   {cagr * 100:.2f}%")
        print("-" * 40)
