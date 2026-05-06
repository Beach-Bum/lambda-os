#pragma once

#include <QtCore/QObject>
#include <QtCore/QString>
#include <QtCore/QVariantMap>
#include "interface.h"

class AgentixLogosInterface : public PluginInterface
{
public:
    virtual ~AgentixLogosInterface() {}

    /// Return read-only workspace state (submodules, branch, dirty status).
    Q_INVOKABLE virtual QVariantMap controllerPlan(const QString &workspacePath) = 0;

    /// Dry-run a controller goal — returns what an execute would do, never mutates.
    Q_INVOKABLE virtual QVariantMap controllerRun(const QString &goal, const QString &workspacePath) = 0;

    /// Return the last N audit events from .agentix/audit.jsonl.
    Q_INVOKABLE virtual QVariantMap auditTail(const QString &workspacePath, int lines) = 0;

    /// Validate the workspace policy and return any violations.
    Q_INVOKABLE virtual QVariantMap policyCheck(const QString &workspacePath) = 0;

    /// Take a source snapshot: submodule SHAs, tracked diff, untracked hashes.
    Q_INVOKABLE virtual QVariantMap snapshot(const QString &workspacePath) = 0;

    /// Verify a module loads in a sandbox via logos_host.
    Q_INVOKABLE virtual QVariantMap verifyModule(const QString &workspacePath, const QString &moduleName) = 0;

signals:
    void eventResponse(const QString &eventName, const QVariantList &data);
};

#define AgentixLogosInterface_iid "org.logos.AgentixLogosInterface"
Q_DECLARE_INTERFACE(AgentixLogosInterface, AgentixLogosInterface_iid)
