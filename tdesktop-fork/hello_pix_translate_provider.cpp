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
		// 这里只认"桥接是否可用"。开关策略归 TranslateTracker 管 —— 只有它知道
		// 这条消息是收的还是发的、在不在群里；Provider 只负责把交给它的文本翻出来。
		// 以前这里还判了 !bridge.transRecv，结果是「关掉接收翻译、只留发送翻译」的
		// 用户连自己发出去的消息也翻不了，而且完全静默。
		if (!bridge.ok) {
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

// 是否强制打开该会话的翻译追踪。
// 只要还想让 Hello Pix 翻任何东西（收或发），追踪就必须开着 —— 追踪关着的话
// TranslateTracker 根本不会向 Provider 要译文，连"只翻我发出去的"也做不到。
// 注意这**不等于**"翻所有消息"：具体收的翻不翻、群的翻不翻，由下面三个开关
// 在 tracker 的 add() 里逐条过滤。
bool HelloPixShouldTrackTranslation() {
	const auto bridge = ReadBridge();
	return bridge.ok && (bridge.transRecv || bridge.transSend);
}

bool HelloPixShouldTranslateIncoming() {
	const auto bridge = ReadBridge();
	return bridge.ok && bridge.transRecv;
}

bool HelloPixShouldTranslateSend() {
	const auto bridge = ReadBridge();
	return bridge.ok && bridge.transSend;
}

bool HelloPixShouldTranslateGroup() {
	const auto bridge = ReadBridge();
	return bridge.ok && bridge.transGroup;
}

LanguageId HelloPixTargetLanguage() {
	return LanguageId::FromName(ReadBridge().lang);
}

std::unique_ptr<TranslateProvider> CreateHelloPixTranslateProvider() {
	// 安装条件与"是否强制追踪"共用同一个判断，这是有意的：
	//   - 以前这里用 HelloPixShouldTranslateIncoming()（只认 transRecv），于是
	//     "关掉接收翻译、只留发送翻译"的用户连 Provider 都没有，整个 Hello Pix
	//     翻译静默失效、回落到电报自带翻译。
	//   - 但也不能改成"桥接在就装"：那样两个开关都关掉时我们的 Provider 会接管
	//     工厂，而 add() 又把所有消息都挡掉，等于把用户自己在电报里开的翻译功能
	//     一起弄哑了。
	//   - 用 transRecv || transSend 正好：还想翻东西就接管，都不翻了就把翻译
	//     交还电报自带的 provider，不越权。
	// 装上了也不会多翻：tracker 的 add() 会按三个开关逐条过滤。
	return HelloPixShouldTrackTranslation()
		? std::make_unique<HelloPixTranslateProvider>()
		: nullptr;
}

} // namespace Ui
