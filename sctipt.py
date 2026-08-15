#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dataset Manager - Полнофункциональный менеджер датасетов для fine-tuning
Консольное приложение с интерактивным меню
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Any, Tuple
from datetime import datetime


# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

VALID_INTENTS = {"TASK", "DATA", "CANCEL", "CONFIRM", "DENIAL", "NONE"}
VALID_DOMAINS = {"ACCESS", "HARDWARE", "SOFTWARE", "DOCS", "INFO", None}

DOMAIN_FIXES = {
    "DOSC": "DOCS",
    "DOC": "DOCS",
    "ACESS": "ACCESS",
    "HARDWARD": "HARDWARE",
    "SOFWARE": "SOFTWARE",
}

# Рекомендуемое количество примеров для качественного обучения
RECOMMENDED_EXAMPLES = {
    "minimum": 300,      # Минимум для базового качества
    "good": 500,         # Хорошее качество
    "excellent": 1000,   # Отличное качество
    "per_class": 20,     # Минимум примеров на класс
    "per_combo": 15,     # Минимум на комбинацию Intent-Domain
}


# ============================================================================
# УТИЛИТЫ
# ============================================================================

def clear_screen():
    """Очистка экрана консоли"""
    import os
    os.system('cls' if os.name == 'nt' else 'clear')


def print_header(text: str, char: str = "="):
    """Вывод заголовка"""
    width = 80
    print("\n" + char * width)
    print(text.center(width))
    print(char * width)


def print_section(text: str):
    """Вывод секции"""
    print("\n" + "─" * 80)
    print(f"📋 {text}")
    print("─" * 80)


def normalize_text(text: str) -> str:
    """Нормализация текста"""
    text = text.replace('\n', ' ')
    text = text.replace('\r', ' ')
    text = text.replace('\t', ' ')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def validate_segment_in_input(segment: str, input_text: str) -> bool:
    """Проверка вхождения segment в input"""
    seg_norm = normalize_text(segment)
    inp_norm = normalize_text(input_text)
    return seg_norm in inp_norm


def fix_segment(segment: str, input_text: str) -> str:
    """Исправление segment"""
    seg_norm = normalize_text(segment)
    inp_norm = normalize_text(input_text)
    
    if seg_norm in inp_norm:
        pos = inp_norm.find(seg_norm)
        return inp_norm[pos:pos+len(seg_norm)]
    
    return seg_norm


# ============================================================================
# ЗАГРУЗКА ДАТАСЕТА
# ============================================================================

def load_dataset(filepath: str) -> List[Tuple[int, Dict]]:
    """Загрузка датасета из JSONL файла"""
    data = []
    errors = []
    
    if not Path(filepath).exists():
        print(f"❌ Файл {filepath} не найден!")
        return data
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            try:
                entry = json.loads(line.strip())
                data.append((i, entry))
            except json.JSONDecodeError as e:
                errors.append(f"Строка {i}: {e}")
    
    if errors:
        print(f"\n⚠️  Ошибки при загрузке:")
        for error in errors[:5]:
            print(f"   {error}")
    
    return data


# ============================================================================
# ВАЛИДАТОР ДАТАСЕТА
# ============================================================================

class DatasetValidator:
    def __init__(self):
        self.stats = {
            "total": 0,
            "fixed_intent_case": 0,
            "fixed_domain_case": 0,
            "fixed_domain_typo": 0,
            "fixed_segment": 0,
            "added_missing_fields": 0,
            "invalid_intent": [],
            "invalid_domain": [],
            "segment_mismatches": [],
            "duplicates": defaultdict(list),
            "warnings": [],
        }
        self.seen_inputs = {}
    
    def validate_and_fix_entry(self, entry: Dict[str, Any], index: int) -> Dict[str, Any]:
        """Валидация и исправление примера"""
        self.stats["total"] += 1
        
        if "input" not in entry:
            raise ValueError(f"Пример #{index}: отсутствует поле 'input'")
        if "output_json" not in entry:
            raise ValueError(f"Пример #{index}: отсутствует поле 'output_json'")
        
        original_input = entry["input"]
        normalized_input = normalize_text(original_input)
        entry["input"] = normalized_input
        
        if normalized_input in self.seen_inputs:
            self.stats["duplicates"][normalized_input].append({
                "first": self.seen_inputs[normalized_input],
                "duplicate": index
            })
            self.stats["warnings"].append(
                f"Пример #{index}: дубликат input (оригинал в #{self.seen_inputs[normalized_input]})"
            )
        else:
            self.seen_inputs[normalized_input] = index
        
        if "analysis" not in entry["output_json"]:
            raise ValueError(f"Пример #{index}: отсутствует 'analysis'")
        
        clean_analysis = []
        for seg_idx, segment in enumerate(entry["output_json"]["analysis"]):
            clean_seg = segment.copy()
            
            # Intent
            if "intent" not in clean_seg or not clean_seg["intent"]:
                self.stats["warnings"].append(f"Пример #{index}, сегмент {seg_idx}: отсутствует intent")
                clean_seg["intent"] = "NONE"
                self.stats["added_missing_fields"] += 1
            else:
                original_intent = clean_seg["intent"]
                clean_seg["intent"] = original_intent.upper()
                if original_intent != clean_seg["intent"]:
                    self.stats["fixed_intent_case"] += 1
                
                if clean_seg["intent"] not in VALID_INTENTS:
                    self.stats["invalid_intent"].append({
                        "index": index,
                        "segment": seg_idx,
                        "value": clean_seg["intent"]
                    })
            
            # Domain
            if "domain" not in clean_seg:
                clean_seg["domain"] = None
                self.stats["added_missing_fields"] += 1
            elif clean_seg["domain"]:
                original_domain = clean_seg["domain"]
                clean_seg["domain"] = original_domain.upper()
                if original_domain != clean_seg["domain"]:
                    self.stats["fixed_domain_case"] += 1
                
                if clean_seg["domain"] in DOMAIN_FIXES:
                    old_domain = clean_seg["domain"]
                    clean_seg["domain"] = DOMAIN_FIXES[old_domain]
                    self.stats["fixed_domain_typo"] += 1
                
                if clean_seg["domain"] not in VALID_DOMAINS:
                    self.stats["invalid_domain"].append({
                        "index": index,
                        "segment": seg_idx,
                        "value": clean_seg["domain"]
                    })
            
            # Segment
            if "segment" not in clean_seg or not clean_seg["segment"]:
                self.stats["warnings"].append(f"Пример #{index}, сегмент {seg_idx}: отсутствует segment")
                clean_seg["segment"] = ""
                self.stats["added_missing_fields"] += 1
            else:
                original_segment = clean_seg["segment"]
                
                if not validate_segment_in_input(original_segment, normalized_input):
                    fixed_segment = fix_segment(original_segment, normalized_input)
                    
                    if validate_segment_in_input(fixed_segment, normalized_input):
                        clean_seg["segment"] = fixed_segment
                        self.stats["fixed_segment"] += 1
                    else:
                        self.stats["segment_mismatches"].append({
                            "index": index,
                            "segment_idx": seg_idx
                        })
                        clean_seg["segment"] = normalize_text(original_segment)
            
            if "payload" not in clean_seg:
                clean_seg["payload"] = None
                self.stats["added_missing_fields"] += 1
            
            if "reasoning" not in clean_seg:
                clean_seg["reasoning"] = ""
                self.stats["added_missing_fields"] += 1
            
            clean_analysis.append(clean_seg)
        
        entry["output_json"]["analysis"] = clean_analysis
        return entry


# ============================================================================
# АНАЛИЗАТОР СТАТИСТИКИ
# ============================================================================

class DatasetAnalyzer:
    def __init__(self, data: List[Tuple[int, Dict]]):
        self.data = data
        self.stats = self._collect_stats()
    
    def _collect_stats(self) -> Dict:
        """Сбор детальной статистики"""
        stats = {
            "total_examples": len(self.data),
            "total_segments": 0,
            "intent_counts": Counter(),
            "domain_counts": Counter(),
            "combo_counts": Counter(),
            "input_lengths": [],
            "output_lengths": [],
            "unique_inputs": set(),
            "duplicates": [],
            "errors": [],
            "missing_combos": set(),
        }
        
        for idx, entry in self.data:
            inp = entry.get("input", "")
            stats["unique_inputs"].add(normalize_text(inp))
            stats["input_lengths"].append(len(inp))
            
            try:
                output = json.loads(entry.get("output", "{}"))
                stats["output_lengths"].append(len(entry.get("output", "")))
                
                if "analysis" in output:
                    for seg in output["analysis"]:
                        stats["total_segments"] += 1
                        intent = seg.get("intent", "")
                        domain = seg.get("domain", "")
                        
                        if intent:
                            stats["intent_counts"][intent] += 1
                        if domain:
                            stats["domain_counts"][domain] += 1
                        if intent and domain:
                            stats["combo_counts"][(intent, domain)] += 1
                        
                        # Проверка segment
                        segment = seg.get("segment", "")
                        if segment and not validate_segment_in_input(segment, inp):
                            stats["errors"].append({
                                "line": idx,
                                "type": "segment_mismatch",
                                "detail": f"Segment не найден в input"
                            })
            except json.JSONDecodeError:
                stats["errors"].append({
                    "line": idx,
                    "type": "json_error",
                    "detail": "Невалидный JSON в output"
                })
        
        # Находим отсутствующие комбинации
        all_combos = set()
        for intent in VALID_INTENTS:
            if intent not in ["NONE", "INFO"]:  # Эти обычно без domain
                for domain in VALID_DOMAINS:
                    if domain:
                        all_combos.add((intent, domain))
        
        existing_combos = set(stats["combo_counts"].keys())
        stats["missing_combos"] = all_combos - existing_combos
        
        return stats
    
    def print_comprehensive_report(self):
        """Всеобъемлющий отчет с рекомендациями"""
        print_header("📊 ВСЕОБЪЕМЛЮЩАЯ СТАТИСТИКА ДАТАСЕТА", "=")
        
        self._print_basic_stats()
        self._print_quality_assessment()
        self._print_intent_analysis()
        self._print_domain_analysis()
        self._print_combo_analysis()
        self._print_length_stats()
        self._print_errors_and_issues()
        self._print_coverage_analysis()
        self._print_recommendations()
        self._print_final_score()
    
    def _print_basic_stats(self):
        """Базовая статистика"""
        print_section("ОБЩАЯ ИНФОРМАЦИЯ")
        
        print(f"📁 Всего примеров: {self.stats['total_examples']}")
        print(f"📝 Всего сегментов: {self.stats['total_segments']}")
        print(f"🔄 Уникальных inputs: {len(self.stats['unique_inputs'])}")
        
        dup_count = self.stats['total_examples'] - len(self.stats['unique_inputs'])
        if dup_count > 0:
            print(f"⚠️  Дубликатов: {dup_count}")
        
        avg_segments = self.stats['total_segments'] / max(self.stats['total_examples'], 1)
        print(f"📊 Среднее сегментов на пример: {avg_segments:.1f}")
    
    def _print_quality_assessment(self):
        """Оценка качества датасета"""
        print_section("ОЦЕНКА КАЧЕСТВА")
        
        total = self.stats['total_examples']
        
        # Оценка размера
        if total < RECOMMENDED_EXAMPLES['minimum']:
            print(f"❌ Размер датасета: {total} (критически мало)")
            print(f"   Минимум: {RECOMMENDED_EXAMPLES['minimum']}")
        elif total < RECOMMENDED_EXAMPLES['good']:
            print(f"⚠️  Размер датасета: {total} (недостаточно)")
            print(f"   Рекомендуется: {RECOMMENDED_EXAMPLES['good']}+")
        elif total < RECOMMENDED_EXAMPLES['excellent']:
            print(f"✅ Размер датасета: {total} (хорошо)")
        else:
            print(f"✅✅ Размер датасета: {total} (отлично)")
        
        # Оценка уникальности
        uniqueness = len(self.stats['unique_inputs']) / total * 100
        if uniqueness < 90:
            print(f"⚠️  Уникальность: {uniqueness:.1f}% (много дубликатов)")
        else:
            print(f"✅ Уникальность: {uniqueness:.1f}%")
        
        # Оценка ошибок
        error_rate = len(self.stats['errors']) / total * 100 if total > 0 else 0
        if error_rate > 5:
            print(f"❌ Ошибки: {error_rate:.1f}% примеров ({len(self.stats['errors'])} шт)")
        elif error_rate > 0:
            print(f"⚠️  Ошибки: {error_rate:.1f}% примеров ({len(self.stats['errors'])} шт)")
        else:
            print(f"✅ Ошибок не найдено")
    
    def _print_intent_analysis(self):
        """Анализ Intent"""
        print_section("РАСПРЕДЕЛЕНИЕ INTENT")
        
        total_intents = sum(self.stats['intent_counts'].values())
        
        print(f"\n{'Intent':<15} {'Кол-во':<10} {'%':<10} {'Статус'}")
        print("─" * 50)
        
        for intent in sorted(VALID_INTENTS):
            count = self.stats['intent_counts'].get(intent, 0)
            percentage = (count / total_intents * 100) if total_intents > 0 else 0
            
            # Определяем статус
            if count == 0:
                status = "❌ Отсутствует"
            elif count < RECOMMENDED_EXAMPLES['per_class']:
                status = f"⚠️  Мало (нужно {RECOMMENDED_EXAMPLES['per_class']}+)"
            else:
                status = "✅"
            
            bar = "█" * int(percentage / 2)
            print(f"{intent:<15} {count:<10} {percentage:>5.1f}%  {bar} {status}")
        
        # Баланс
        if total_intents > 0:
            max_count = max(self.stats['intent_counts'].values())
            min_count = min(self.stats['intent_counts'].values()) if self.stats['intent_counts'] else 0
            balance_ratio = max_count / max(min_count, 1)
            
            print(f"\n📊 Баланс классов (max/min): {balance_ratio:.1f}")
            if balance_ratio > 5:
                print("   ⚠️  Сильный дисбаланс! Рекомендуется <= 3.0")
            elif balance_ratio > 3:
                print("   ⚠️  Умеренный дисбаланс")
            else:
                print("   ✅ Хороший баланс")
    
    def _print_domain_analysis(self):
        """Анализ Domain"""
        print_section("РАСПРЕДЕЛЕНИЕ DOMAIN")
        
        total_domains = sum(self.stats['domain_counts'].values())
        
        print(f"\n{'Domain':<15} {'Кол-во':<10} {'%':<10} {'Статус'}")
        print("─" * 50)
        
        for domain in sorted(d for d in VALID_DOMAINS if d):
            count = self.stats['domain_counts'].get(domain, 0)
            percentage = (count / total_domains * 100) if total_domains > 0 else 0
            
            if count == 0:
                status = "❌ Отсутствует"
            elif count < RECOMMENDED_EXAMPLES['per_class']:
                status = f"⚠️  Мало"
            else:
                status = "✅"
            
            bar = "█" * int(percentage / 2)
            print(f"{domain:<15} {count:<10} {percentage:>5.1f}%  {bar} {status}")
    
    def _print_combo_analysis(self):
        """Анализ комбинаций Intent-Domain"""
        print_section("КОМБИНАЦИИ INTENT × DOMAIN")
        
        print(f"\nТоп-15 комбинаций:")
        print(f"{'Intent':<10} {'Domain':<15} {'Кол-во':<10} {'Статус'}")
        print("─" * 50)
        
        for (intent, domain), count in self.stats['combo_counts'].most_common(15):
            if count < RECOMMENDED_EXAMPLES['per_combo']:
                status = f"⚠️  Мало (нужно {RECOMMENDED_EXAMPLES['per_combo']}+)"
            else:
                status = "✅"
            
            print(f"{intent:<10} {domain:<15} {count:<10} {status}")
        
        # Отсутствующие комбинации
        if self.stats['missing_combos']:
            print(f"\n❌ Отсутствуют комбинации ({len(self.stats['missing_combos'])} шт):")
            for i, (intent, domain) in enumerate(list(self.stats['missing_combos'])[:10], 1):
                print(f"   {i}. {intent} + {domain}")
            
            if len(self.stats['missing_combos']) > 10:
                print(f"   ... и еще {len(self.stats['missing_combos']) - 10}")
    
    def _print_length_stats(self):
        """Статистика по длинам"""
        print_section("СТАТИСТИКА ПО ДЛИНАМ")
        
        if self.stats['input_lengths']:
            avg_input = sum(self.stats['input_lengths']) / len(self.stats['input_lengths'])
            min_input = min(self.stats['input_lengths'])
            max_input = max(self.stats['input_lengths'])
            
            print(f"\n📏 Длина Input (символов):")
            print(f"   Минимум: {min_input}")
            print(f"   Максимум: {max_input}")
            print(f"   Среднее: {avg_input:.0f}")
            print(f"   Токены (примерно): ~{avg_input/4:.0f}")
            
            # Оценка
            if max_input > 2000:
                print("   ⚠️  Есть очень длинные примеры (>2000 символов)")
        
        if self.stats['output_lengths']:
            avg_output = sum(self.stats['output_lengths']) / len(self.stats['output_lengths'])
            print(f"\n📄 Длина Output (символов): ~{avg_output:.0f}")
    
    def _print_errors_and_issues(self):
        """Ошибки и проблемы"""
        if not self.stats['errors']:
            return
        
        print_section("ОШИБКИ И ПРОБЛЕМЫ")
        
        error_types = Counter(e['type'] for e in self.stats['errors'])
        
        print(f"\n❌ Найдено ошибок: {len(self.stats['errors'])}\n")
        
        for error_type, count in error_types.most_common():
            print(f"   {error_type}: {count} шт")
            
            # Показываем примеры
            examples = [e for e in self.stats['errors'] if e['type'] == error_type][:3]
            for ex in examples:
                print(f"      Строка {ex['line']}: {ex['detail']}")
    
    def _print_coverage_analysis(self):
        """Анализ покрытия"""
        print_section("АНАЛИЗ ПОКРЫТИЯ")
        
        total = self.stats['total_examples']
        
        # Покрытие Intent
        covered_intents = len([i for i in VALID_INTENTS if self.stats['intent_counts'].get(i, 0) > 0])
        intent_coverage = covered_intents / len(VALID_INTENTS) * 100
        
        print(f"\n✓ Покрытие Intent: {covered_intents}/{len(VALID_INTENTS)} ({intent_coverage:.0f}%)")
        
        # Покрытие Domain
        valid_domains = [d for d in VALID_DOMAINS if d]
        covered_domains = len([d for d in valid_domains if self.stats['domain_counts'].get(d, 0) > 0])
        domain_coverage = covered_domains / len(valid_domains) * 100
        
        print(f"✓ Покрытие Domain: {covered_domains}/{len(valid_domains)} ({domain_coverage:.0f}%)")
        
        # Покрытие комбинаций
        total_possible_combos = len(VALID_INTENTS) * len(valid_domains)
        covered_combos = len(self.stats['combo_counts'])
        combo_coverage = covered_combos / total_possible_combos * 100
        
        print(f"✓ Покрытие комбинаций: {covered_combos}/{total_possible_combos} ({combo_coverage:.0f}%)")
        
        if combo_coverage < 50:
            print("   ⚠️  Низкое покрытие! Много комбинаций отсутствуют")
    
    def _print_recommendations(self):
        """Рекомендации по улучшению"""
        print_section("РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ")
        
        recommendations = []
        total = self.stats['total_examples']
        
        # Размер датасета
        if total < RECOMMENDED_EXAMPLES['minimum']:
            needed = RECOMMENDED_EXAMPLES['minimum'] - total
            recommendations.append(
                f"🎯 КРИТИЧНО: Добавить {needed}+ примеров (сейчас {total}, нужно минимум {RECOMMENDED_EXAMPLES['minimum']})"
            )
        elif total < RECOMMENDED_EXAMPLES['good']:
            needed = RECOMMENDED_EXAMPLES['good'] - total
            recommendations.append(
                f"📈 Добавить {needed}+ примеров для лучшего качества (цель: {RECOMMENDED_EXAMPLES['good']})"
            )
        
        # Дубликаты
        dup_count = total - len(self.stats['unique_inputs'])
        if dup_count > total * 0.05:  # Больше 5% дубликатов
            recommendations.append(
                f"🔄 Удалить {dup_count} дубликатов для повышения разнообразия"
            )
        
        # Редкие классы Intent
        rare_intents = [
            intent for intent, count in self.stats['intent_counts'].items()
            if 0 < count < RECOMMENDED_EXAMPLES['per_class']
        ]
        if rare_intents:
            recommendations.append(
                f"⚖️  Добавить примеры для редких Intent: {', '.join(rare_intents)}"
            )
        
        # Отсутствующие Intent
        missing_intents = [
            intent for intent in VALID_INTENTS
            if self.stats['intent_counts'].get(intent, 0) == 0
        ]
        if missing_intents:
            recommendations.append(
                f"❌ Добавить примеры для отсутствующих Intent: {', '.join(missing_intents)}"
            )
        
        # Редкие комбинации
        rare_combos = [
            f"{intent}+{domain}" for (intent, domain), count in self.stats['combo_counts'].items()
            if count < RECOMMENDED_EXAMPLES['per_combo']
        ]
        if rare_combos:
            recommendations.append(
                f"📊 Увеличить количество примеров для {len(rare_combos)} комбинаций (< {RECOMMENDED_EXAMPLES['per_combo']} шт)"
            )
        
        # Отсутствующие комбинации
        if self.stats['missing_combos']:
            recommendations.append(
                f"🆕 Добавить примеры для {len(self.stats['missing_combos'])} отсутствующих комбинаций"
            )
        
        # Ошибки
        if self.stats['errors']:
            recommendations.append(
                f"🔧 Исправить {len(self.stats['errors'])} ошибок в датасете"
            )
        
        # Вывод рекомендаций
        if recommendations:
            print("\n" + "\n".join(f"{i}. {rec}" for i, rec in enumerate(recommendations, 1)))
            
            # Приоритеты
            print("\n" + "─" * 80)
            print("📌 ПРИОРИТЕТЫ:")
            print("   1. Исправить критические ошибки")
            print("   2. Увеличить размер датасета до минимума")
            print("   3. Добавить отсутствующие классы")
            print("   4. Сбалансировать редкие комбинации")
        else:
            print("\n✅ Датасет в хорошем состоянии! Можно начинать обучение.")
    
    def _print_final_score(self):
        """Итоговая оценка"""
        print_section("ИТОГОВАЯ ОЦЕНКА")
        
        score = 100
        issues = []
        
        # Размер
        total = self.stats['total_examples']
        if total < RECOMMENDED_EXAMPLES['minimum']:
            score -= 30
            issues.append("Критически малый размер")
        elif total < RECOMMENDED_EXAMPLES['good']:
            score -= 15
            issues.append("Недостаточный размер")
        
        # Ошибки
        error_rate = len(self.stats['errors']) / total * 100 if total > 0 else 0
        if error_rate > 5:
            score -= 20
            issues.append("Много ошибок")
        elif error_rate > 0:
            score -= 10
        
        # Дубликаты
        dup_rate = (total - len(self.stats['unique_inputs'])) / total * 100 if total > 0 else 0
        if dup_rate > 10:
            score -= 15
            issues.append("Много дубликатов")
        elif dup_rate > 5:
            score -= 5
        
        # Покрытие
        covered_intents = len([i for i in VALID_INTENTS if self.stats['intent_counts'].get(i, 0) > 0])
        if covered_intents < len(VALID_INTENTS):
            missing = len(VALID_INTENTS) - covered_intents
            score -= missing * 5
            issues.append(f"Отсутствуют {missing} Intent")
        
        # Баланс
        if self.stats['intent_counts']:
            max_count = max(self.stats['intent_counts'].values())
            min_count = min(self.stats['intent_counts'].values()) if self.stats['intent_counts'] else 1
            balance_ratio = max_count / max(min_count, 1)
            if balance_ratio > 5:
                score -= 15
                issues.append("Сильный дисбаланс классов")
            elif balance_ratio > 3:
                score -= 10
        
        score = max(0, score)
        
        # Вывод оценки
        print(f"\n{'='*80}")
        print(f"  ОЦЕНКА: {score}/100".center(80))
        print(f"{'='*80}")
        
        if score >= 90:
            print("\n  ✅✅ ОТЛИЧНЫЙ датасет! Production-ready.")
        elif score >= 75:
            print("\n  ✅ ХОРОШИЙ датасет. Минимальные доработки.")
        elif score >= 60:
            print("\n  ⚠️  СРЕДНИЙ датасет. Требуются улучшения.")
        elif score >= 40:
            print("\n  ⚠️⚠️ СЛАБЫЙ датасет. Необходимы серьезные доработки.")
        else:
            print("\n  ❌ ПЛОХОЙ датасет. Требуется полная переработка.")
        
        if issues:
            print("\n  Основные проблемы:")
            for issue in issues:
                print(f"    • {issue}")


# ============================================================================
# ФУНКЦИЯ ПОДГОТОВКИ JSONL
# ============================================================================

def prepare_jsonl():
    """Режим подготовки JSONL датасета"""
    clear_screen()
    print_header("📦 ПОДГОТОВКА JSONL ДАТАСЕТА")
    
    # Загрузка существующего датасета
    print("\n1️⃣  Загрузка исходного датасета")
    filepath = input("Введите путь к JSONL файлу (или Enter для dataset.jsonl): ").strip()
    if not filepath:
        filepath = "dataset.jsonl"
    
    data = load_dataset(filepath)
    if not data:
        print("❌ Не удалось загрузить датасет")
        input("\nНажмите Enter для продолжения...")
        return
    
    print(f"✅ Загружено {len(data)} примеров")
    
    # Преобразование формата
    print("\n2️⃣  Преобразование формата...")
    original_data = []
    system_instruction = None
    
    for idx, entry in data:
        if system_instruction is None and "instruction" in entry:
            system_instruction = entry["instruction"]
        
        try:
            original_data.append({
                "input": entry["input"],
                "output_json": json.loads(entry["output"])
            })
        except (KeyError, json.JSONDecodeError) as e:
            print(f"⚠️  Ошибка в строке {idx}: {e}")
    
    if not system_instruction:
        print("\n⚠️  System instruction не найдена в датасете")
        system_instruction = input("Введите system instruction (или Enter для пропуска): ").strip()
    
    # Валидация и исправление
    print("\n3️⃣  Валидация и исправление...")
    validator = DatasetValidator()
    processed_data = []
    
    for i, entry in enumerate(original_data):
        try:
            clean_entry = validator.validate_and_fix_entry(entry, i + 1)
            processed_data.append(clean_entry)
        except Exception as e:
            print(f"❌ Пример #{i+1}: {str(e)}")
    
    # Вывод статистики валидации
    validator.print_summary()
    
    # Сохранение
    print("\n4️⃣  Сохранение...")
    output_file = input("Имя выходного файла (Enter для dataset_clean.jsonl): ").strip()
    if not output_file:
        output_file = "dataset_clean.jsonl"
    
    with open(output_file, "w", encoding="utf-8") as f:
        for entry in processed_data:
            output_str = json.dumps(entry["output_json"], ensure_ascii=False)
            final_entry = {
                "instruction": system_instruction,
                "input": entry["input"],
                "output": output_str
            }
            f.write(json.dumps(final_entry, ensure_ascii=False) + "\n")
    
    print(f"\n✅ Датасет сохранен: {output_file}")
    print(f"📊 Обработано примеров: {len(processed_data)}")
    
    input("\nНажмите Enter для продолжения...")


# ============================================================================
# ФУНКЦИЯ СТАТИСТИКИ
# ============================================================================

def show_statistics():
    """Режим отображения статистики"""
    clear_screen()
    print_header("📊 СТАТИСТИКА ДАТАСЕТА")
    
    filepath = input("Введите путь к JSONL файлу (или Enter для dataset.jsonl): ").strip()
    if not filepath:
        filepath = "dataset.jsonl"
    
    print(f"\n🔍 Анализирую {filepath}...")
    
    data = load_dataset(filepath)
    if not data:
        print("❌ Не удалось загрузить датасет")
        input("\nНажмите Enter для продолжения...")
        return
    
    analyzer = DatasetAnalyzer(data)
    analyzer.print_comprehensive_report()
    
    # Сохранить отчет?
    print("\n" + "="*80)
    save = input("\n💾 Сохранить отчет в файл? (y/n): ").strip().lower()
    
    if save == 'y':
        report_file = f"dataset_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        import sys
        from io import StringIO
        
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        
        analyzer.print_comprehensive_report()
        
        report_content = sys.stdout.getvalue()
        sys.stdout = old_stdout
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report_content)
        
        print(f"✅ Отчет сохранен: {report_file}")
    
    input("\nНажмите Enter для продолжения...")


# ============================================================================
# ФУНКЦИЯ ЭКСПОРТА В JSONL
# ============================================================================

def export_to_jsonl():
    """Режим экспорта JSON в JSONL формат"""
    clear_screen()
    print_header("📤 ЭКСПОРТ В JSONL")
    
    print("\nЭта функция конвертирует JSON файлы в JSONL формат")
    print("Например: synthetic_examples.json → synthetic_examples.jsonl\n")
    
    # Выбор входного файла
    print("1️⃣  Выбор входного файла")
    input_file = input("Введите путь к JSON файлу (или Enter для synthetic_examples.json): ").strip()
    if not input_file:
        input_file = "synthetic_examples.json"
    
    # Проверка существования файла
    if not Path(input_file).exists():
        print(f"❌ Файл {input_file} не найден!")
        input("\nНажмите Enter для продолжения...")
        return
    
    # Загрузка JSON
    print(f"\n📂 Загружаю {input_file}...")
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Проверяем формат
        if not isinstance(data, list):
            print("❌ JSON должен содержать массив примеров!")
            input("\nНажмите Enter для продолжения...")
            return
        
        print(f"✅ Загружено {len(data)} примеров")
        
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка чтения JSON: {e}")
        input("\nНажмите Enter для продолжения...")
        return
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        input("\nНажмите Enter для продолжения...")
        return
    
    # System instruction
    print("\n2️⃣  System instruction")
    print("Введите system instruction или оставьте пустым для значения по умолчанию\n")
    
    default_instruction = """Ты — экспертный NLU-анализатор для корпоративной техподдержки. 
Твоя задача — разобрать сообщение пользователя и определить:
1. Intent (намерение): TASK, DATA, CANCEL, CONFIRM, DENIAL, NONE
2. Domain (область): ACCESS, HARDWARE, SOFTWARE, DOCS, INFO
3. Payload (суть запроса)
4. Reasoning (объяснение)

Ответ должен быть в формате JSON."""
    
    system_instruction = input("System instruction (Enter для по умолчанию): ").strip()
    if not system_instruction:
        system_instruction = default_instruction
        print("✅ Использована instruction по умолчанию")
    
    # Валидация данных
    print("\n3️⃣  Валидация данных...")
    valid_data = []
    errors = []
    
    for i, entry in enumerate(data, 1):
        try:
            # Проверяем обязательные поля
            if "input" not in entry:
                errors.append(f"Пример #{i}: отсутствует поле 'input'")
                continue
            
            if "output_json" not in entry:
                errors.append(f"Пример #{i}: отсутствует поле 'output_json'")
                continue
            
            # Проверяем структуру output_json
            if "analysis" not in entry["output_json"]:
                errors.append(f"Пример #{i}: отсутствует 'analysis' в output_json")
                continue
            
            valid_data.append(entry)
            
        except Exception as e:
            errors.append(f"Пример #{i}: {str(e)}")
    
    if errors:
        print(f"\n⚠️  Найдены ошибки ({len(errors)}):")
        for error in errors[:5]:
            print(f"   {error}")
        if len(errors) > 5:
            print(f"   ... и еще {len(errors) - 5}")
    
    print(f"\n✅ Валидных примеров: {len(valid_data)} из {len(data)}")
    
    if not valid_data:
        print("❌ Нет валидных данных для экспорта!")
        input("\nНажмите Enter для продолжения...")
        return
    
    # Выбор выходного файла
    print("\n4️⃣  Сохранение")
    output_file = input("Имя выходного файла (Enter для auto): ").strip()
    if not output_file:
        # Автоматическое имя на основе входного файла
        base_name = Path(input_file).stem
        output_file = f"{base_name}.jsonl"
    
    # Экспорт в JSONL
    print(f"\n💾 Экспортирую в {output_file}...")
    
    try:
        exported_count = 0
        with open(output_file, 'w', encoding='utf-8') as f:
            for entry in valid_data:
                # Конвертируем output_json в строку
                output_str = json.dumps(entry["output_json"], ensure_ascii=False)
                
                # Создаем финальную запись
                jsonl_entry = {
                    "instruction": system_instruction,
                    "input": entry["input"],
                    "output": output_str
                }
                
                # Записываем в JSONL
                f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")
                exported_count += 1
        
        print(f"✅ Успешно экспортировано: {exported_count} примеров")
        print(f"📁 Файл сохранен: {output_file}")
        
        # Предложить объединить с основным датасетом
        print("\n" + "─" * 80)
        print("💡 Совет: Теперь вы можете объединить файлы:")
        print(f"   cat dataset.jsonl {output_file} > dataset_merged.jsonl")
        print("   Или используйте Python для объединения и удаления дубликатов")
        
    except Exception as e:
        print(f"❌ Ошибка при экспорте: {e}")
    
    input("\nНажмите Enter для продолжения...")


# ============================================================================
# ФУНКЦИЯ ЭКСПОРТА ИЗ PYTHON ПЕРЕМЕННОЙ
# ============================================================================

def export_from_python_data():
    """Режим экспорта из переменной data в Python файле"""
    clear_screen()
    print_header("🐍 ЭКСПОРТ ИЗ PYTHON ПЕРЕМЕННОЙ")
    
    print("\nЭта функция экспортирует переменную 'data' из Python файла в JSONL")
    print("Ваш Python файл должен содержать переменную data = [...]")
    print()
    
    # Выбор входного файла
    print("1️⃣  Выбор Python файла")
    input_file = input("Введите путь к .py файлу (или Enter для data.py): ").strip()
    if not input_file:
        input_file = "data.py"
    
    # Проверка существования файла
    if not Path(input_file).exists():
        print(f"❌ Файл {input_file} не найден!")
        input("\nНажмите Enter для продолжения...")
        return
    
    # Загрузка данных из Python файла
    print(f"\n📂 Загружаю {input_file}...")
    try:
        # Читаем содержимое файла
        with open(input_file, 'r', encoding='utf-8') as f:
            file_content = f.read()
        
        # Ищем system_instruction в начале файла (до data = )
        system_instruction = None
        
        # Паттерн для поиска system_instruction = """...""" или '''...'''
        pattern = r'system_instruction\s*=\s*["\']""(.*?)""["\']|system_instruction\s*=\s*["\']\'\'(.*?)\'\'\["\']'
        match = re.search(r'system_instruction\s*=\s*"""(.*?)"""', file_content, re.DOTALL)
        if not match:
            match = re.search(r"system_instruction\s*=\s*'''(.*?)'''", file_content, re.DOTALL)
        
        if match:
            system_instruction = match.group(1).strip()
            print(f"✅ Найден system_instruction в файле ({len(system_instruction)} символов)")
        
        # Создаем namespace для выполнения кода
        namespace = {}
        
        # Выполняем код файла
        exec(file_content, namespace)
        
        # Проверяем наличие переменной data
        if 'data' not in namespace:
            print("❌ В файле не найдена переменная 'data'!")
            print("\nФайл должен содержать: data = [...]")
            input("\nНажмите Enter для продолжения...")
            return
        
        data = namespace['data']
        
        # Проверяем формат
        if not isinstance(data, list):
            print("❌ Переменная 'data' должна быть списком!")
            input("\nНажмите Enter для продолжения...")
            return
        
        print(f"✅ Загружено {len(data)} примеров из переменной 'data'")
        
    except SyntaxError as e:
        print(f"❌ Синтаксическая ошибка в Python файле: {e}")
        input("\nНажмите Enter для продолжения...")
        return
    except Exception as e:
        print(f"❌ Ошибка при загрузке: {e}")
        import traceback
        traceback.print_exc()
        input("\nНажмите Enter для продолжения...")
        return
    
    # System instruction
    print("\n2️⃣  System instruction")
    
    if system_instruction:
        print(f"✅ System instruction найден в начале файла")
        print(f"   Длина: {len(system_instruction)} символов")
        preview = system_instruction[:100].replace('\n', ' ')
        print(f"   Начало: {preview}...")
        use_from_file = input("\nИспользовать его? (y/n, Enter=y): ").strip().lower()
        if use_from_file in ['n', 'no', 'нет']:
            system_instruction = None
    
    if not system_instruction:
        print("\nВведите system instruction или оставьте пустым для значения по умолчанию\n")
        
        default_instruction = """Ты — NLU-анализатор корпоративной техподдержки.

        ЗАДАЧА: Разбить сообщение на смысловые сегменты и классифицировать каждый.

        СТРУКТУРА ОТВЕТА:
        {"analysis": [
        {
            "segment": "точная подстрока из input (с опечатками!)",
            "intent": "TASK|DATA|CANCEL|CONFIRM|DENIAL|NONE",
            "domain": "ACCESS|HARDWARE|SOFTWARE|DOCS|INFO|null",
            "payload": "краткое описание в инфинитиве или null",
            "reasoning": "почему выбран этот intent и domain"
        }
        ]}

        INTENT:
        - TASK: глагол действия (дай, настрой, сделай) ИЛИ неявная просьба (не работает = помоги)
        - DATA: изолированная информация БЕЗ глагола (номера, ID, hostname)
        - CANCEL: отмена задачи/тикета
        - CONFIRM: согласие, подтверждение
        - DENIAL: отказ, несогласие
        - NONE: приветствия, эмоции, вода

        DOMAIN:
        - ACCESS: доступы, пароли, логины, системы (ПГС, ФРГУ, 1С-вход)
        - HARDWARE: железо, сеть, принтеры, мыши, клавиатуры, серверы
        - SOFTWARE: программы (1С, Excel, браузеры, VK Teams)
        - DOCS: ЭЦП, сертификаты, PDF, документы, КриптоПро
        - INFO: справочные вопросы (как?, где?, что?)
        - null: когда domain неприменим

        ПРАВИЛА:
        1. Segment = ТОЧНАЯ подстрока (с опечатками!)
        2. Один сегмент = одно намерение
        3. Эмоции отдельно от запросов
        4. Payload = краткий инфинитив БЕЗ вежливости, опечатки исправлены
        5. Для NONE/CONFIRM/DENIAL: payload обычно null
        ПРИМЕР:
        Input: "Привет! Не могу войти в ПГС"
        Output: {"analysis": [
        {"segment": "Привет", "intent": "NONE", "domain": null, "payload": null, "reasoning": "Приветствие"},
        {"segment": "Не могу войти в ПГС", "intent": "TASK", "domain": "ACCESS", "payload": "проблема с доступом в ПГС", "reasoning": "Неявный запрос помощи со входом в систему"}
        ]}"""
                
        system_instruction = input("System instruction (Enter для по умолчанию): ").strip()
        if not system_instruction:
            system_instruction = default_instruction
            print("✅ Использована instruction по умолчанию")
    
    # Валидация данных
    print("\n3️⃣  Валидация данных...")
    valid_data = []
    errors = []
    
    for i, entry in enumerate(data, 1):
        try:
            # Проверяем обязательные поля
            if "input" not in entry:
                errors.append(f"Пример #{i}: отсутствует поле 'input'")
                continue
            
            if "output_json" not in entry:
                errors.append(f"Пример #{i}: отсутствует поле 'output_json'")
                continue
            
            # Проверяем структуру output_json
            if "analysis" not in entry["output_json"]:
                errors.append(f"Пример #{i}: отсутствует 'analysis' в output_json")
                continue
            
            # Проверяем, что analysis - это список
            if not isinstance(entry["output_json"]["analysis"], list):
                errors.append(f"Пример #{i}: 'analysis' должен быть списком")
                continue
            
            # Проверяем каждый сегмент
            for j, seg in enumerate(entry["output_json"]["analysis"]):
                required_fields = ["segment", "intent", "domain", "payload", "reasoning"]
                for field in required_fields:
                    if field not in seg:
                        errors.append(f"Пример #{i}, сегмент {j+1}: отсутствует поле '{field}'")
            
            valid_data.append(entry)
            
        except Exception as e:
            errors.append(f"Пример #{i}: {str(e)}")
    
    if errors:
        print(f"\n⚠️  Найдены ошибки ({len(errors)}):")
        for error in errors[:10]:
            print(f"   {error}")
        if len(errors) > 10:
            print(f"   ... и еще {len(errors) - 10}")
        print()
    
    print(f"✅ Валидных примеров: {len(valid_data)} из {len(data)}")
    
    if not valid_data:
        print("❌ Нет валидных данных для экспорта!")
        input("\nНажмите Enter для продолжения...")
        return
    
    # Выбор выходного файла
    print("\n4️⃣  Сохранение")
    output_file = input("Имя выходного файла (Enter для dataset_from_python.jsonl): ").strip()
    if not output_file:
        output_file = "dataset_from_python.jsonl"
    
    # Экспорт в JSONL
    print(f"\n💾 Экспортирую в {output_file}...")
    
    try:
        exported_count = 0
        with open(output_file, 'w', encoding='utf-8') as f:
            for entry in valid_data:
                # Конвертируем output_json в строку
                output_str = json.dumps(entry["output_json"], ensure_ascii=False)
                
                # Создаем финальную запись
                jsonl_entry = {
                    "instruction": system_instruction,
                    "input": entry["input"],
                    "output": output_str
                }
                
                # Записываем в JSONL
                f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")
                exported_count += 1
        
        print(f"✅ Успешно экспортировано: {exported_count} примеров")
        print(f"📁 Файл сохранен: {output_file}")
        
        # Дополнительная статистика
        print("\n" + "─" * 80)
        print("📊 СТАТИСТИКА:")
        print("─" * 80)
        print(f"   Источник: {input_file}")
        print(f"   Переменная: data")
        print(f"   Всего примеров: {len(data)}")
        print(f"   Валидных: {len(valid_data)}")
        print(f"   Ошибок: {len(errors)}")
        print("─" * 80)
        
        # Предложить дальнейшие действия
        print("\n💡 Следующие шаги:")
        print("   1. Проверить статистику: python dataset_manager.py → 2")
        print(f"   2. Объединить с основным: python dataset_manager.py → 4")
        print("   3. Подготовить финал: python dataset_manager.py → 1")
        
    except Exception as e:
        print(f"❌ Ошибка при экспорте: {e}")
        import traceback
        traceback.print_exc()
    
    input("\nНажмите Enter для продолжения...")


# ============================================================================
# ФУНКЦИЯ ОБЪЕДИНЕНИЯ JSONL
# ============================================================================

def merge_jsonl():
    """Режим объединения нескольких JSONL файлов"""
    clear_screen()
    print_header("🔗 ОБЪЕДИНЕНИЕ JSONL ФАЙЛОВ")
    
    print("\nЭта функция объединяет несколько JSONL файлов с удалением дубликатов\n")
    
    # Сбор файлов для объединения
    print("1️⃣  Добавление файлов")
    print("Введите пути к файлам (по одному на строку)")
    print("Оставьте пустую строку для завершения\n")
    
    files = []
    while True:
        filepath = input(f"Файл #{len(files)+1} (Enter для завершения): ").strip()
        if not filepath:
            break
        
        if not Path(filepath).exists():
            print(f"   ⚠️  Файл {filepath} не найден, пропускаем")
            continue
        
        files.append(filepath)
        print(f"   ✅ Добавлен: {filepath}")
    
    if len(files) < 2:
        print("\n❌ Нужно минимум 2 файла для объединения!")
        input("\nНажмите Enter для продолжения...")
        return
    
    print(f"\n📊 Будет объединено файлов: {len(files)}")
    
    # Загрузка и объединение данных
    print("\n2️⃣  Загрузка данных...")
    all_data = []
    file_stats = []
    
    for filepath in files:
        data = load_dataset(filepath)
        file_stats.append({
            "file": filepath,
            "count": len(data),
            "data": data
        })
        all_data.extend(data)
        print(f"   📂 {filepath}: {len(data)} примеров")
    
    total_before = len(all_data)
    print(f"\n📊 Всего примеров до объединения: {total_before}")
    
    # Удаление дубликатов
    print("\n3️⃣  Удаление дубликатов...")
    
    seen_inputs = set()
    unique_data = []
    duplicates = []
    
    for idx, entry in all_data:
        inp = normalize_text(entry.get("input", ""))
        
        if inp in seen_inputs:
            duplicates.append((idx, inp[:50]))
        else:
            seen_inputs.add(inp)
            unique_data.append((idx, entry))
    
    total_after = len(unique_data)
    removed = total_before - total_after
    
    print(f"   ✅ Уникальных примеров: {total_after}")
    print(f"   🗑️  Удалено дубликатов: {removed}")
    
    if duplicates and removed > 0:
        print(f"\n   Примеры дубликатов:")
        for idx, inp_preview in duplicates[:5]:
            print(f"      Строка {idx}: {inp_preview}...")
        if len(duplicates) > 5:
            print(f"      ... и еще {len(duplicates) - 5}")
    
    # Сохранение
    print("\n4️⃣  Сохранение результата")
    output_file = input("Имя выходного файла (Enter для dataset_merged.jsonl): ").strip()
    if not output_file:
        output_file = "dataset_merged.jsonl"
    
    print(f"\n💾 Сохраняю в {output_file}...")
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            for idx, entry in unique_data:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        
        print(f"✅ Успешно сохранено: {total_after} примеров")
        print(f"📁 Файл: {output_file}")
        
        # Статистика
        print("\n" + "─" * 80)
        print("📊 ИТОГОВАЯ СТАТИСТИКА:")
        print("─" * 80)
        for stat in file_stats:
            print(f"   {stat['file']}: {stat['count']} примеров")
        print("─" * 80)
        print(f"   Всего до: {total_before}")
        print(f"   Дубликатов: -{removed}")
        print(f"   Итого: {total_after} примеров")
        print("─" * 80)
        
    except Exception as e:
        print(f"❌ Ошибка при сохранении: {e}")
    
    input("\nНажмите Enter для продолжения...")


# ============================================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================================

def main_menu():
    """Главное меню приложения"""
    while True:
        clear_screen()
        print_header("🚀 DATASET MANAGER", "=")
        print("\nМенеджер датасетов для fine-tuning Qwen 2.5 3B\n")
        
        print("Выберите действие:\n")
        print("  1️⃣  Подготовка JSONL")
        print("      • Загрузка датасета")
        print("      • Валидация и исправление")
        print("      • Нормализация текстов")
        print("      • Сохранение в чистом виде")
        print()
        print("  2️⃣  Всеобъемлющая статистика")
        print("      • Анализ качества")
        print("      • Распределение классов")
        print("      • Поиск ошибок и дубликатов")
        print("      • Рекомендации по улучшению")
        print()
        print("  3️⃣  Экспорт JSON в JSONL")
        print("      • Конвертация JSON файла → JSONL")
        print("      • Добавление system instruction")
        print("      • Валидация данных")
        print()
        print("  4️⃣  Экспорт из Python переменной")
        print("      • Экспорт data = [...] → JSONL")
        print("      • Чтение .py файла")
        print("      • Автоматическая валидация")
        print()
        print("  5️⃣  Объединение JSONL")
        print("      • Слияние нескольких файлов")
        print("      • Удаление дубликатов")
        print("      • Создание единого датасета")
        print()
        print("  6️⃣  Выход")
        print()
        print("="*80)
        
        choice = input("\nВведите номер (1-6): ").strip()
        
        if choice == '1':
            prepare_jsonl()
        elif choice == '2':
            show_statistics()
        elif choice == '3':
            export_to_jsonl()
        elif choice == '4':
            export_from_python_data()
        elif choice == '5':
            merge_jsonl()
        elif choice == '6':
            print("\n👋 До свидания!")
            break
        else:
            print("\n❌ Неверный выбор. Попробуйте снова.")
            input("Нажмите Enter для продолжения...")


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\n👋 Прервано пользователем. До свидания!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)