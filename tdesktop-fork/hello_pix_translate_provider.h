#pragma once

#include "translate_provider.h"

namespace Ui {

[[nodiscard]] bool HelloPixBridgeEnabled();
[[nodiscard]] bool HelloPixShouldTranslateIncoming();
[[nodiscard]] bool HelloPixShouldTranslateGroup();
[[nodiscard]] LanguageId HelloPixTargetLanguage();
[[nodiscard]] std::unique_ptr<TranslateProvider> CreateHelloPixTranslateProvider();

} // namespace Ui
