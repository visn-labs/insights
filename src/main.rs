mod api;
mod config;
mod domain;
mod event_engine;
mod gemma;
mod pipeline;
mod sink;
mod store;
mod ui;

use std::{net::SocketAddr, sync::Arc};

use anyhow::Context;
use config::Config;
use gemma::GemmaClient;
use pipeline::PipelineService;
use sink::build_sink;
use store::AppState;
use tokio::net::TcpListener;
use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let config = Arc::new(Config::from_env()?);
    init_telemetry(&config.log_filter);

    tokio::fs::create_dir_all(config.upload_dir())
        .await
        .context("create upload data directory")?;

    let gemma = GemmaClient::new(config.clone())?;
    let sink = build_sink(config.clone())?;
    let pipeline = PipelineService::new(config.clone(), gemma, sink);
    let state = AppState::new(config.clone(), pipeline);
    let app = api::router(state);

    let address: SocketAddr = config.bind.parse().context("parse VISN_BIND")?;
    let listener = TcpListener::bind(address)
        .await
        .context("bind HTTP listener")?;
    info!(%address, "visn Phase 0 service ready");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .context("serve HTTP")
}

fn init_telemetry(filter: &str) {
    let env_filter = tracing_subscriber::EnvFilter::try_new(filter)
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
    tracing_subscriber::registry()
        .with(env_filter)
        .with(tracing_subscriber::fmt::layer().json())
        .init();
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("install Ctrl-C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("install terminate handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}
