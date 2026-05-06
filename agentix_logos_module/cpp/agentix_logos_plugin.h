#pragma once

#include <QtCore/QObject>
#include <QtCore/QString>
#include <QtCore/QVariantMap>
#include <QtCore/QProcess>
#include "agentix_logos_interface.h"
#include "logos_api.h"

/// Logos module exposing Agentix's safety primitives to the runtime.
///
/// Every method shells out to the `agentix-logos` CLI and returns parsed
/// JSON. The module is deliberately read-only / dry-run only — actual
/// mutations require human approval via the upstream `agentix` CLI.
///
/// This is the first module that governs other modules.
class AgentixLogosPlugin : public QObject, public AgentixLogosInterface
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID AgentixLogosInterface_iid FILE "../metadata.json")
    Q_INTERFACES(AgentixLogosInterface PluginInterface)

public:
    AgentixLogosPlugin();
    ~AgentixLogosPlugin();

    // PluginInterface
    QString name() const override { return "agentix_logos_module"; }
    QString version() const override { return "0.1.0"; }
    Q_INVOKABLE void initLogos(LogosAPI *logosAPIInstance);

    // AgentixLogosInterface — all read-only / dry-run
    Q_INVOKABLE QVariantMap controllerPlan(const QString &workspacePath) override;
    Q_INVOKABLE QVariantMap controllerRun(const QString &goal, const QString &workspacePath) override;
    Q_INVOKABLE QVariantMap auditTail(const QString &workspacePath, int lines) override;
    Q_INVOKABLE QVariantMap policyCheck(const QString &workspacePath) override;
    Q_INVOKABLE QVariantMap snapshot(const QString &workspacePath) override;
    Q_INVOKABLE QVariantMap verifyModule(const QString &workspacePath, const QString &moduleName) override;

signals:
    void eventResponse(const QString &eventName, const QVariantList &data);

private:
    /// Run the agentix-logos CLI with the given arguments and return parsed JSON.
    QVariantMap invoke(const QString &method, const QStringList &args);

    /// Resolve the agentix-logos binary path.
    QString resolveBin() const;

    LogosAPI *m_logosAPI = nullptr;
    QString m_binPath;
    int m_timeoutMs = 60000; // 60 second default
};
