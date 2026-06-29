export interface ExchangeSettingsView {
  api_key: string;
  api_key_set: boolean;
  secret: string;
  secret_set: boolean;
  passphrase: string;
  passphrase_set: boolean;
  market_type: string;
  demo: boolean;
}

export interface ExchangeSettingsUpdate {
  api_key: string;
  secret: string;
  passphrase: string;
  market_type: string;
  demo: boolean;
}

export interface BacktestSettings {
  initial_capital: number;
  fee_rate: number;
  slippage: number;
  data_cache_dir: string;
}

export interface RiskSettings {
  max_daily_loss_pct: number;
  max_drawdown_pct: number;
  max_total_position_pct: number;
  allow_live_open_orders: boolean;
  live_max_order_notional: number;
}

export interface NotifySettingsView {
  telegram_bot_token: string;
  telegram_bot_token_set: boolean;
  telegram_chat_id: string;
}

export interface NotifySettingsUpdate {
  telegram_bot_token: string;
  telegram_chat_id: string;
}

export interface WebSettings {
  host: string;
  port: number;
}

export interface AppSettingsView {
  mode: string;
  exchange: ExchangeSettingsView;
  backtest: BacktestSettings;
  risk: RiskSettings;
  notify: NotifySettingsView;
  web: WebSettings;
}

export interface AppSettingsUpdate {
  mode: string;
  exchange: ExchangeSettingsUpdate;
  backtest: BacktestSettings;
  risk: RiskSettings;
  notify: NotifySettingsUpdate;
  web: WebSettings;
}
