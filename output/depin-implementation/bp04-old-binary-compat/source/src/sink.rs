use std::sync::Arc;

#[cfg(feature = "kafka")]
use std::time::Duration;

#[cfg(not(feature = "kafka"))]
use anyhow::bail;
use async_trait::async_trait;
#[cfg(feature = "kafka")]
use serde_json::json;
use uuid::Uuid;

use crate::{
    config::Config,
    domain::{ObservedEvent, Report},
};

#[async_trait]
pub trait EventSink: Send + Sync {
    async fn publish_event(&self, job_id: Uuid, event: &ObservedEvent) -> anyhow::Result<()>;
    async fn publish_report(&self, job_id: Uuid, report: &Report) -> anyhow::Result<()>;
    fn name(&self) -> &'static str;
}

pub fn build_sink(config: Arc<Config>) -> anyhow::Result<Arc<dyn EventSink>> {
    if !config.kafka_enabled {
        return Ok(Arc::new(NoopSink));
    }

    #[cfg(feature = "kafka")]
    {
        Ok(Arc::new(KafkaSink::new(config)?))
    }

    #[cfg(not(feature = "kafka"))]
    {
        bail!("VISN_KAFKA_ENABLED=true but this binary was built without --features kafka")
    }
}

struct NoopSink;

#[async_trait]
impl EventSink for NoopSink {
    async fn publish_event(&self, _job_id: Uuid, _event: &ObservedEvent) -> anyhow::Result<()> {
        Ok(())
    }

    async fn publish_report(&self, _job_id: Uuid, _report: &Report) -> anyhow::Result<()> {
        Ok(())
    }

    fn name(&self) -> &'static str {
        "disabled"
    }
}

#[cfg(feature = "kafka")]
struct KafkaSink {
    producer: rdkafka::producer::FutureProducer,
    event_topic: String,
    report_topic: String,
}

#[cfg(feature = "kafka")]
impl KafkaSink {
    fn new(config: Arc<Config>) -> anyhow::Result<Self> {
        use rdkafka::config::ClientConfig;

        let producer = ClientConfig::new()
            .set("bootstrap.servers", &config.kafka_brokers)
            .set("enable.idempotence", "true")
            .set("acks", "all")
            .set("compression.type", "zstd")
            .create()?;
        Ok(Self {
            producer,
            event_topic: config.kafka_event_topic.clone(),
            report_topic: config.kafka_report_topic.clone(),
        })
    }

    async fn send<T: serde::Serialize>(
        &self,
        topic: &str,
        job_id: Uuid,
        kind: &str,
        payload: &T,
    ) -> anyhow::Result<()> {
        use rdkafka::producer::FutureRecord;

        let envelope = json!({
            "schema_version": 1,
            "message_id": Uuid::now_v7(),
            "job_id": job_id,
            "kind": kind,
            "payload": payload
        });
        let body = serde_json::to_string(&envelope)?;
        let key = job_id.to_string();
        self.producer
            .send(
                FutureRecord::to(topic).key(&key).payload(&body),
                Duration::from_secs(10),
            )
            .await
            .map_err(|(error, _)| error)?;
        Ok(())
    }
}

#[cfg(feature = "kafka")]
#[async_trait]
impl EventSink for KafkaSink {
    async fn publish_event(&self, job_id: Uuid, event: &ObservedEvent) -> anyhow::Result<()> {
        self.send(&self.event_topic, job_id, "event.observed", event)
            .await
    }

    async fn publish_report(&self, job_id: Uuid, report: &Report) -> anyhow::Result<()> {
        self.send(&self.report_topic, job_id, "insight.completed", report)
            .await
    }

    fn name(&self) -> &'static str {
        "kafka"
    }
}
