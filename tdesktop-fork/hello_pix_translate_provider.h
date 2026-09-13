#pragma once

#include "translate_provider.h"

namespace Ui {

[[nodiscard]] bool HelloPixBridgeEnabled();
[[nodiscard]] bool HelloPixShouldTrackTranslation();
[[nodiscard]] bool HelloPixShouldTranslateIncoming();
[[nodiscard]] bool HelloPixShouldTranslateSend();
[[nodiscard]] bool HelloPixShouldTranslateGroup();
[[nodiscard]] LanguageId HelloPixTargetLanguage();
[[nodiscard]] std::unique_ptr<TranslateProvider> CreateHelloPixTranslateProvider();

} // namespace Ui
