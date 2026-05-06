#include "agentix_logos_plugin.h"

#include <QCoreApplication>
#include <QDebug>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QStandardPaths>

AgentixLogosPlugin::AgentixLogosPlugin()
{
    m_binPath = resolveBin();
    if (m_binPath.isEmpty()) {
        qWarning() << "agentix_logos_module: agentix-logos binary not found on PATH."
                    << "Install with: uv tool install agentix-logos";
    } else {
        qDebug() << "agentix_logos_module: initialized, binary at" << m_binPath;
    }
}

AgentixLogosPlugin::~AgentixLogosPlugin()
{
    if (m_logosAPI) {
        delete m_logosAPI;
        m_logosAPI = nullptr;
    }
}

void AgentixLogosPlugin::initLogos(LogosAPI *logosAPIInstance)
{
    if (m_logosAPI) {
        delete m_logosAPI;
    }
    m_logosAPI = logosAPIInstance;
}

// ─── Public methods ──────────────────────────────────────────

QVariantMap AgentixLogosPlugin::controllerPlan(const QString &workspacePath)
{
    return invoke("controllerPlan",
                  {"workspace-status", "--path", workspacePath, "--json"});
}

QVariantMap AgentixLogosPlugin::controllerRun(const QString &goal,
                                               const QString &workspacePath)
{
    auto plan = invoke("controllerRun",
                       {"workspace-status", "--path", workspacePath, "--json"});
    if (!plan.value("ok").toBool())
        return plan;

    // Wrap in a controller envelope — dry-run only from module surface
    QVariantMap envelope;
    envelope["mode"] = "dry-run";
    envelope["from_module"] = true;
    envelope["goal"] = goal;
    envelope["workspace"] = workspacePath;
    envelope["workspace_state"] = plan.value("data");
    envelope["execute_hint"] = QString(
        "To execute, run: agentix controller-run \"%1\" --path %2 --execute "
        "from a human terminal. The module surface is dry-run only by design.")
        .arg(goal, workspacePath);

    QVariantMap result;
    result["ok"] = true;
    result["exit_code"] = 0;
    result["method"] = "controllerRun";
    result["data"] = envelope;
    return result;
}

QVariantMap AgentixLogosPlugin::auditTail(const QString &workspacePath, int lines)
{
    int n = qBound(0, lines, 1000);
    return invoke("auditTail",
                  {"audit", "tail", "--path", workspacePath,
                   "--lines", QString::number(n), "--json"});
}

QVariantMap AgentixLogosPlugin::policyCheck(const QString &workspacePath)
{
    return invoke("policyCheck",
                  {"policy-check", "--path", workspacePath, "--json"});
}

QVariantMap AgentixLogosPlugin::snapshot(const QString &workspacePath)
{
    return invoke("snapshot",
                  {"snapshot", "--path", workspacePath, "--json"});
}

QVariantMap AgentixLogosPlugin::verifyModule(const QString &workspacePath,
                                              const QString &moduleName)
{
    return invoke("verifyModule",
                  {"verify-logoscore",
                   "--workspace", workspacePath,
                   "--modules", moduleName,
                   "--call", moduleName + ".load()",
                   "--backend", "auto",
                   "--timeout", "10",
                   "--json"});
}

// ─── Internals ───────────────────────────────────────────────

QVariantMap AgentixLogosPlugin::invoke(const QString &method,
                                        const QStringList &args)
{
    QVariantMap result;
    result["method"] = method;

    if (m_binPath.isEmpty()) {
        result["ok"] = false;
        result["exit_code"] = 127;
        result["data"] = QVariantMap();
        result["error"] = "agentix-logos binary not found. "
                          "Install with: uv tool install agentix-logos";
        return result;
    }

    QProcess proc;
    proc.setProgram(m_binPath);
    proc.setArguments(args);
    proc.start();

    if (!proc.waitForFinished(m_timeoutMs)) {
        proc.kill();
        proc.waitForFinished(3000);
        result["ok"] = false;
        result["exit_code"] = 124;
        result["data"] = QVariantMap();
        result["error"] = QString("timeout after %1ms").arg(m_timeoutMs);
        return result;
    }

    int exitCode = proc.exitCode();
    QByteArray stdoutData = proc.readAllStandardOutput();
    QByteArray stderrData = proc.readAllStandardError();

    if (exitCode != 0) {
        result["ok"] = false;
        result["exit_code"] = exitCode;
        result["data"] = QVariantMap();
        result["error"] = QString::fromUtf8(stderrData).right(2000);
        return result;
    }

    QJsonParseError parseError;
    QJsonDocument doc = QJsonDocument::fromJson(stdoutData, &parseError);
    if (parseError.error != QJsonParseError::NoError || !doc.isObject()) {
        result["ok"] = false;
        result["exit_code"] = exitCode;
        result["data"] = QVariantMap();
        result["error"] = "CLI returned non-JSON: " + parseError.errorString();
        return result;
    }

    result["ok"] = true;
    result["exit_code"] = 0;
    result["data"] = doc.object().toVariantMap();
    return result;
}

QString AgentixLogosPlugin::resolveBin() const
{
    // Check PATH first
    QString found = QStandardPaths::findExecutable("agentix-logos");
    if (!found.isEmpty())
        return found;

    // Common uv tool install locations
    QStringList candidates = {
        QDir::homePath() + "/.local/bin/agentix-logos",
        "/usr/local/bin/agentix-logos",
    };
    for (const auto &c : candidates) {
        if (QFile::exists(c))
            return c;
    }
    return {};
}
