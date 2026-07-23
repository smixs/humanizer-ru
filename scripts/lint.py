#!/usr/bin/env python3
"""Детерминированный линтер AI-слопа для humanizer-ru (v3).

Использование:
    python3 scripts/lint.py file.md      # или stdin: python3 scripts/lint.py < file.md
    python3 scripts/lint.py --self-test

ERROR  = жёсткие запреты SKILL.md + артефакты копипаста из чат-ботов
         (гейт: exit 1, текст не готов).
WARN   = маркеры паттернов и метрики ритма (оценивать кластерами, exit 0).
Вердикт по severity: errors*3 + warnings -> clean (0-3) / review (4-10) / rewrite (11+).
Линт гоняется ТОЛЬКО по чистовому тексту - без changelog и цитат «до».
Артефакт в бэктиках не считается: так артефакты цитируют, а не копипастят.
"""
import re
import sys

# --- класс A: артефакты копипаста из чат-ботов (мгновенный вердикт) ---
# Порт из Vladimir-Human/humanizer-ru (MIT); regex там проверены fixtures.
# Гоняются по сырому тексту (включая URL), но без code-блоков и бэктиков.
ARTIFACTS = [
    ("A oaicite-сноска", re.compile(r":contentReference\[oaicite:\d+\]|oai_citation:\d+‡|\boaicite:\d+")),
    ("A turn-метка", re.compile(r"\bturn\d+(?:search|file|fetch|image|news|video|ref)\d+|citeturn")),
    ("A utm/referrer чат-бота", re.compile(r"utm_source=(?:chatgpt|copilot)\.com|referrer=grok\.com")),
    ("A grok-карточка", re.compile(r"grok_card://|grok_render_citation_card_json|<grok-card\b")),
    ("A gemini-цитата", re.compile(r"vertexaisearch\S*grounding-api-redirect|\[cite_start\]|\[cite:\s*\d+|\[span_\d+\]")),
    ("A внутренняя сноска", re.compile(r"【\d+†[^】]*】|\]\(sandbox:/mnt/data/")),
    ("A остаток размышлений", re.compile(r"</?think>")),
    ("A perplexity-upload", re.compile(r"ppl-ai-file-upload")),
    ("A placeholder", re.compile(r"INSERT_SOURCE_URL|PASTE_\w+_URL_HERE|\bURL_HERE\b|\b20\d\d-XX-XX\b")),
    ("A PUA-метка", re.compile(r"[-]")),
]
CODE_STRIP = re.compile(r"```.*?```|`[^`\n]+`", re.S)  # цитируемые артефакты не считаем
# символы нулевой ширины - класс B (бывают от CMS и рассылок), поэтому WARN;
# ZWJ (U+200D) внутри эмодзи-последовательности - норма, ловим только вне эмодзи
EMOJI_CH = "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️\U0001F3FB-\U0001F3FF]"
ZERO_WIDTH = re.compile(r"[​‌⁠﻿]|(?<!%s)‍|‍(?!%s)" % (EMOJI_CH, EMOJI_CH))

# --- жёсткие запреты (номера паттернов из references/patterns.md) ---
ERRORS = [
    ("23 длинное тире", re.compile(r"[—–]")),
    ("23 мат-знаки", re.compile(r"(?:[≈≥≤≠±⇒←→]|\s[=><&+]\s|\d\+(?!\d)|\bvs\.?\b)")),
    ("13 негативный параллелизм", re.compile(
        r"[Нн]е (?:просто|только)\b(?:[^.!?\n]{0,80}?\bно и\b)?|[Рр]ечь идёт не только|"
        r"[Нн]ет [^,.!?\n]{1,40}, нет ")),
    ("27 рубленый драматизм", re.compile(r"(?:Без|Ноль) [^.!?\n]{1,35}[.!] (?:Без|Ноль) ")),
]
HR_LINE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")

# --- маркеры для судейского прохода (кластеры решают, не одиночные хиты) ---
WARN_PHRASES = [
    # 3 избегание «это»
    "представляет собой", "выступает в роли", "служит основой", "знаменует собой",
    # 6 AI-словарь (стемы ловят словоформы)
    "ключев", "важнейш", "знаменует", "демонстрир", "способств", "подчёркива",
    "свидетельств", "неуклонно",
    # 10 размытые атрибуции
    "по мнению экспертов", "аналитики отмечают", "исследователи утверждают",
    # 11 шаблонные переходы
    "важно отметить", "следует подчеркнуть", "необходимо учитывать",
    "стоит обратить внимание", "нельзя не упомянуть",
    # 12 вызовы и перспективы
    "сталкивается с рядом вызовов", "несмотря на эти вызовы",
    # 17-18 подобострастие и артефакты чатбота
    "отличный вопрос", "надеюсь, это поможет", "надеюсь, было полезно",
    "дайте знать", "буду рад помочь",
    # 20 позитивные заключения
    "будущее выглядит ярким", "впереди захватывающие времена", "продолжает процветать",
    # 22 стоп-слова
    "в современном мире", "на сегодняшний день", "в настоящее время", "как известно",
    "не секрет, что", "ни для кого не секрет", "каждый из нас",
    # 25 псевдоглубина (+ faux-insight сетапы)
    "по сути", "если копнуть глубже", "глубинная проблема", "настоящий вопрос в том",
    "в конечном счёте", "все упускают", "большинство упускает", "никто не расскажет",
    "никто не говорит о", "главная ошибка большинства", "чего вам не расскажут",
    # 26 анонсы
    "давайте разберёмся", "погрузимся в", "вот что нужно знать", "без лишних слов",
    # 29 фальшивая доверительность
    "скажу прямо", "давайте начистоту", "вот в чём штука", "если по-честному",
    # 37 псевдо-терапевтический регистр
    "и это нормально", "и это окей", "вы не одиноки", "давайте признаем",
    "позвольте себе",
    # 31 резюме
    "подводя итог", "в заключение", "резюмируя",
    # 32 спекуляции
    "широко не задокументирован", "предположительно",
    # 34 стопка абзацев (фразы-склейки без связи)
    "кроме того", "более того", "также стоит", "ещё один аспект", "ещё одним",
]
WARN_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
# 19: три и более смягчения в одном предложении = каскад (одно-два - норма речи)
SOFTENERS = ("возможно", "вероятно", "по-видимому", "как правило", "в некоторых случаях",
             "скорее всего", "при определённых условиях", "обычно", "в зависимости от",
             "в большинстве случаев", "потенциально")
# colon reveal: «подводка: драматичное раскрытие» - только явные формы,
# чтобы не бить по спискам, меткам и обычным двоеточиям
COLON_REVEAL = re.compile(
    r"(?:[Сс]амое (?:интересное|главное|важное)|[Лл]учшая часть|[Гг]лавная деталь|"
    r"[Фф]ишка в том|[Дд]еталь, которая [^:\n]{0,35})\s*:")
BOLD_SPAN = re.compile(r"\*\*[^*\n]+\*\*")
INFORMAL = re.compile(r"\b(ты|вы|тебе|вам|твой|твоя|ваш|ваша|вами|тобой)\b", re.I)
# ponytail: стем-эвристика по глагольным суффиксам, морфологию не тянем;
# апгрейд до pymorphy - если станет много ложных срабатываний
VERB_SUFFIX = re.compile(r"(ует|яет|ает|еет|ит|ат|ят|ют|ал|ял|ил|ел|ся|сь|ть)$")

STRIP = re.compile(r"```.*?```|`[^`\n]+`|https?://\S+", re.S)  # код и URL не проза


def strip_frontmatter(lines):
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 40)):
            if lines[i].strip() == "---":
                return [""] * (i + 1) + lines[i + 1:]
    return lines


def prose_sentences(lines):
    """Предложения из прозаических строк (без заголовков, таблиц, списков)."""
    prose = " ".join(
        l for l in lines
        if l.strip() and not re.match(r"^\s*(#|\||[-*+]\s|\d+\.\s|>)", l))
    prose = re.sub(r"\*\*|«|»", "", prose)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", prose) if s.strip()]


def verb_stems(sentence):
    stems = set()
    for w in re.findall(r"[а-яё]{5,}", sentence.lower()):
        if VERB_SUFFIX.search(w):
            stems.add(VERB_SUFFIX.sub("", w)[:6])
    return {s for s in stems if len(s) >= 4}


def lint(text):
    findings = []  # (kind, line_no, rule, excerpt)
    text = text.lstrip("﻿")  # BOM - артефакт кодировки, не текста

    # класс A: по сырому тексту (URL нужны для utm/referrer), но без бэктиков
    raw = CODE_STRIP.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    for i, line in enumerate(strip_frontmatter(raw.splitlines()), 1):
        for rule, rx in ARTIFACTS:
            for m in rx.finditer(line):
                ctx = line[max(0, m.start() - 25):m.end() + 25].strip()
                findings.append(("ERROR", i, rule, ctx))
        if ZERO_WIDTH.search(line):
            findings.append(("WARN", i, "B символ нулевой ширины",
                             "невидимый символ (CMS и рассылки тоже их ставят - проверь источник)"))

    clean = STRIP.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    lines = strip_frontmatter(clean.splitlines())

    for i, line in enumerate(lines, 1):
        if HR_LINE.match(line):
            findings.append(("ERROR", i, "34 разделитель в теле", line.strip()[:40]))
            continue
        scan = re.sub(r"^\s*[>+*]\s", "  ", line)  # markdown-маркеры не прозаические знаки
        for rule, rx in ERRORS:
            for m in rx.finditer(scan):
                ctx = scan[max(0, m.start() - 25):m.end() + 25].strip()
                findings.append(("ERROR", i, rule, ctx))
        low = scan.lower()
        for phrase in WARN_PHRASES:
            if phrase in low:
                findings.append(("WARN", i, phrase, scan.strip()[:70]))
        if WARN_EMOJI.search(scan):
            findings.append(("WARN", i, "21 эмодзи", scan.strip()[:70]))
        if COLON_REVEAL.search(scan):
            findings.append(("WARN", i, "двоеточие-подводка", scan.strip()[:70]))

    sents = prose_sentences(lines)
    lengths = [len(s.split()) for s in sents]

    # 19: каскад смягчений - три и более уклончивых слова в одном предложении
    for s in sents:
        low = s.lower()
        hits = sum(low.count(w) for w in SOFTENERS)
        if hits >= 3:
            findings.append(("WARN", 0, "19 каскад смягчений", f"{hits} смягчения: {s[:60]}"))

    # 33: повтор глагольной основы в соседних предложениях
    for a, b in zip(range(len(sents) - 1), range(1, len(sents))):
        common = verb_stems(sents[a]) & verb_stems(sents[b])
        if common:
            findings.append(("WARN", 0, "33 повтор глагола",
                             f"«{sorted(common)[0]}…» в соседних предложениях: {sents[b][:50]}"))

    # ритм (burstiness): монотонность и отсутствие коротких предложений
    if len(lengths) >= 8:
        diffs = [abs(x - y) for x, y in zip(lengths, lengths[1:])]
        mean_diff = sum(diffs) / len(diffs)
        if mean_diff < 4:
            findings.append(("WARN", 0, "ритм монотонный",
                             f"средняя разница длин соседних предложений {mean_diff:.1f} слова (живой текст: 6+)"))
        if len(lengths) >= 10 and not any(l <= 8 for l in lengths):
            findings.append(("WARN", 0, "ритм без коротких",
                             "ни одного предложения до 8 слов - нет пауз и акцентов"))

    # плотность жирного: максимум ~1 на 200 слов
    words_total = sum(lengths)
    bold_count = len(BOLD_SPAN.findall(text))
    if words_total >= 200 and bold_count > words_total / 200 + 1:
        findings.append(("WARN", 0, "жирный перебор",
                         f"{bold_count} жирных на {words_total} слов (норма ~{max(1, words_total // 200)})"))

    # формальное открытие: первые 3 предложения без единого неформального хода
    head = sents[:6]
    if len(head) >= 3:
        informal = (any(INFORMAL.search(s) for s in head)
                    or any("?" in s for s in head)
                    or any(len(s.split()) <= 8 for s in head))
        if not informal:
            findings.append(("WARN", 0, "формальное открытие",
                             "в начале нет ни обращения, ни вопроса, ни короткой фразы"))

    return findings


def verdict(errors, warnings):
    score = errors * 3 + warnings
    if score <= 3:
        return score, "clean"
    if score <= 10:
        return score, "review - посмотри warnings кластерами"
    return score, "rewrite - слопа слишком много для точечных правок"


def self_test():
    bad = "Это не просто курс — это экосистема. Скорость > идеальности. Без кода. Без настроек. Итог ≈ 5+ часов, джуны vs сеньоры."
    kinds = [f[2] for f in lint(bad) if f[0] == "ERROR"]
    assert any("13" in k for k in kinds), kinds
    assert any("тире" in k for k in kinds), kinds
    assert any("мат-знаки" in k for k in kinds), kinds
    assert any("27" in k for k in kinds), kinds

    ok = "Обычный текст - с коротким тире, без слопа. Цифры 12 и 87 на месте.\n> цитата\n+ пункт списка"
    assert not [f for f in lint(ok) if f[0] == "ERROR"], lint(ok)

    warn = "Важно отметить, что по сути будущее выглядит ярким."
    assert len([f for f in lint(warn) if f[0] == "WARN"]) >= 3

    hr = "---\ntitle: x\n---\n\nАбзац первый про дело.\n\n---\n\nАбзац второй про другое."
    hr_hits = [f for f in lint(hr) if f[0] == "ERROR" and "разделитель" in f[2]]
    assert len(hr_hits) == 1, hr_hits  # frontmatter не считается, разделитель в теле - да

    verbs = "Сбербанк предлагает проверять адрес каждого перевода внимательно. Тинькофф предлагает подтверждать операцию отдельным кодом всегда."
    assert any("33" in f[2] for f in lint(verbs)), lint(verbs)

    mono = " ".join(["Это предложение содержит ровно семь слов подряд." ] * 12)
    assert any("ритм" in f[2] for f in lint(mono)), lint(mono)

    # класс A: артефакты копипаста ловятся, в том числе внутри URL
    art = ("Рынок вырос :contentReference[oaicite:0]{index=0}, детали turn0search3, "
           "см. https://example.com/?utm_source=chatgpt.com и [cite: 8].")
    kinds = [f[2] for f in lint(art) if f[0] == "ERROR"]
    assert any("oaicite" in k for k in kinds), kinds
    assert any("turn" in k for k in kinds), kinds
    assert any("utm" in k for k in kinds), kinds
    assert any("gemini" in k for k in kinds), kinds

    # артефакт в бэктиках - цитирование, не копипаст
    art_ok = "Статья разбирает метки `turn0search0` и `</think>` как признаки ИИ."
    assert not [f for f in lint(art_ok) if f[0] == "ERROR"], lint(art_ok)

    # ZWJ внутри эмодзи - норма; голый zero-width - WARN
    fam = "Семья 👨‍👩‍👧 поехала на дачу."
    assert not [f for f in lint(fam) if "нулевой ширины" in f[2]], lint(fam)
    zw = "Обычный текст с невидимым​символом внутри."
    assert any("нулевой ширины" in f[2] for f in lint(zw)), lint(zw)

    # 19: каскад смягчений - три в одном предложении да, одно - нет
    soft = "Возможно, в некоторых случаях это, скорее всего, сработает."
    assert any("каскад" in f[2] for f in lint(soft)), lint(soft)
    soft_ok = "Возможно, это сработает."
    assert not [f for f in lint(soft_ok) if "каскад" in f[2]], lint(soft_ok)

    # двоеточие-подводка
    cr = "Самое интересное: агент учится сам."
    assert any("подводка" in f[2] for f in lint(cr)), lint(cr)
    cr_ok = "Список покупок: хлеб, молоко."
    assert not [f for f in lint(cr_ok) if "подводка" in f[2]], lint(cr_ok)

    print("self-test: OK")


def main():
    if "--self-test" in sys.argv:
        return self_test()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    text = open(args[0], encoding="utf-8").read() if args else sys.stdin.read()
    findings = lint(text)
    errors = [f for f in findings if f[0] == "ERROR"]
    warnings = [f for f in findings if f[0] == "WARN"]
    for kind, line_no, rule, ctx in findings:
        loc = f"строка {line_no}" if line_no else "текст"
        print(f"{kind} {loc}: [{rule}] {ctx}")
    score, v = verdict(len(errors), len(warnings))
    print(f"\nитого: {len(errors)} errors, {len(warnings)} warnings, severity {score} -> {v}")
    if errors:
        print("ГЕЙТ НЕ ПРОЙДЕН - текст не готов, чини errors и запускай снова.")
        sys.exit(1)
    print("гейт пройден: жёстких запретов нет.")


if __name__ == "__main__":
    main()
