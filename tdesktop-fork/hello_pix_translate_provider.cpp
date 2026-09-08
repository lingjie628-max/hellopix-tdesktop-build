#include "lang/hello_pix_translate_provider.h"

#include "settings.h"

#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QJsonParseError>
#include <QtCore/QUrl>
#include <QtCore/QUrlQuery>
#include <QtNetwork/QNetworkAccessManager>
#include <QtNetwork/QNetworkReply>
#include <QtNetwork/QNetworkRequest>

namespace Ui {
namespace {

struct Bridge {
	QString apiBase;
	QString deviceId;
	QString cookie;
	QString csrf;
	QString engine = u"youdao"_q;
	QString lang = u"zh"_q;
	bool transRecv = true;
	bool transSend = true;
	bool transGroup = true;
	bool ok = false;
};

[[nodiscard]] QString BridgePath() {
	return cWorkingDir() + u"hello-pix-bridge.json"_q;
}

[[nodiscard]] Bridge ReadBridge() {
	auto result = Bridge();
	QFile file(BridgePath());
	if (!file.open(QIODevice::ReadOnly)) {
		return result;
	}
	auto error = QJsonParseError();
	const auto parsed = QJsonDocument::fromJson(file.readAll(), &error);
	if (error.error != QJsonParseError::NoError || !parsed.isObject()) {
		return result;
	}
	const auto object = parsed.object();
	result.apiBase = object.value(u"apiBase"_q).toString().trimmed();
	while (result.apiBase.endsWith('/')) {
		result.apiBase.chop(1);
	}
	result.deviceId = object.value(u"deviceId"_q).toString();
	result.engine = object.value(u"engine"_q).toString(u"youdao"_q);
	result.lang = object.value(u"lang"_q).toString(u"zh"_q);
	result.transRecv = object.value(u"transRecv"_q).toBool(true);
	result.transSend = object.value(u"transSend"_q).toBool(true);
	result.transGroup = object.value(u"transGroup"_q).toBool(true);
	const auto cookies = object.value(u"cookies"_q).toObject();
	auto parts = QStringList();
	for (auto i = cookies.constBegin(); i != cookies.constEnd(); ++i) {
		const auto value = i.value().toString();
		if (value.isEmpty()) {
			continue;
		}
		parts.push_back(i.key() + '=' + value);
		if (i.key().contains(u"csrf"_q, Qt::CaseInsensitive)) {
			result.csrf = QUrl::fromPercentEncoding(value.toUtf8());
		}
	}
	result.cookie = parts.join(u"; "_q);
	result.ok = !result.apiBase.isEmpty();
	return result;
}

class HelloPixTranslateProvider final : public TranslateProvider {
public:
	[[nodiscard]] bool supportsMessageId() const override {
		return false;
	}

	void request(
			TranslateProviderRequest request,
			LanguageId to,
			Fn<void(TranslateProviderResult)> done) override {
		const auto original = request.text.text.trimmed();
		if (original.isEmpty()) {
			done(TranslateProviderResult{
				.error = TranslateProviderError::Unknown,
			});
			return;
		}
		const auto bridge = ReadBridge();
		if (!bridge.ok || !bridge.transRecv) {
			done(TranslateProviderResult{
				.error = TranslateProviderError::Unknown,
			});
			return;
		}
		auto url = QUrl(bridge.apiBase + u"/apis/translate/run"_q);
		if (!url.isValid()) {
			done(TranslateProviderResult{
				.error = TranslateProviderError::Unknown,
			});
			return;
		}
		auto networkRequest = QNetworkRequest(url);
		networkRequest.setHeader(
			QNetworkRequest::ContentTypeHeader,
			u"application/x-www-form-urlencoded"_q);
		networkRequest.setRawHeader("Accept", "application/json");
		if (!bridge.deviceId.isEmpty()) {
			networkRequest.setRawHeader(
				"X-Pix-Device",
				bridge.deviceId.toUtf8());
		}
		if (!bridge.cookie.isEmpty()) {
			networkRequest.setRawHeader("Cookie", bridge.cookie.toUtf8());
		}
		if (!bridge.csrf.isEmpty()) {
			networkRequest.setRawHeader(
				"X-CSRF-Token",
				bridge.csrf.toUtf8());
		}
		const auto key = u"tg:%1:%2:%3"_q
			.arg(request.peerId)
			.arg(request.msgId)
			.arg(qHash(original));
		networkRequest.setRawHeader("Idempotency-Key", key.toUtf8());

		QUrlQuery form;
		form.addQueryItem(u"text"_q, original);
		form.addQueryItem(u"from"_q, u"auto"_q);
		form.addQueryItem(
			u"to"_q,
			to.known() ? to.twoLetterCode() : bridge.lang);
		form.addQueryItem(u"engine"_q, bridge.engine);

		const auto reply = _network.post(
			networkRequest,
			form.toString(QUrl::FullyEncoded).toUtf8());
		QObject::connect(reply, &QNetworkReply::finished, [=] {
			auto result = TranslateProviderResult();
			if (reply->error() != QNetworkReply::NoError) {
				result.error = TranslateProviderError::Unknown;
				done(std::move(result));
				reply->deleteLater();
				return;
			}
			auto parseError = QJsonParseError();
			const auto parsed = QJsonDocument::fromJson(
				reply->readAll(),
				&parseError);
			const auto object = parsed.object();
			const auto translation = object.value(u"translation"_q).toString(
				object.value(u"text"_q).toString()).trimmed();
			const auto failed = (parseError.error != QJsonParseError::NoError)
				|| !object.value(u"success"_q).toBool(true)
				|| translation.isEmpty()
				|| (translation.compare(original, Qt::CaseInsensitive) == 0);
			if (failed) {
				result.error = TranslateProviderError::Unknown;
			} else {
				result.text = TextWithEntities{
					original + u"\n[译文] "_q + translation,
				};
			}
			done(std::move(result));
			reply->deleteLater();
		});
	}

private:
	QNetworkAccessManager _network;
};

} // namespace

bool HelloPixBridgeEnabled() {
	return ReadBridge().ok;
}

bool HelloPixShouldTranslateIncoming() {
	const auto bridge = ReadBridge();
	return bridge.ok && bridge.transRecv;
}

bool HelloPixShouldTranslateGroup() {
	const auto bridge = ReadBridge();
	return bridge.ok && bridge.transGroup;
}

LanguageId HelloPixTargetLanguage() {
	return LanguageId::FromName(ReadBridge().lang);
}

std::unique_ptr<TranslateProvider> CreateHelloPixTranslateProvider() {
	return HelloPixShouldTranslateIncoming()
		? std::make_unique<HelloPixTranslateProvider>()
		: nullptr;
}

} // namespace Ui
