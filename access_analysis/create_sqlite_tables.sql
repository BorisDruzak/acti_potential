-- SQLite таблицы на основе Access структуры

-- Таблица: Т_ВидДокумента (2 записей)
CREATE TABLE IF NOT EXISTS Т_ВидДокумента (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
Вид документа TEXT
);

-- Таблица: Т_Исполнитель (311 записей)
CREATE TABLE IF NOT EXISTS Т_Исполнитель (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
Исполнитель TEXT
);

-- Таблица: Т_КтоПодписал (9 записей)
CREATE TABLE IF NOT EXISTS Т_КтоПодписал (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
Кто подписал TEXT
);

-- Таблица: Т_НормативныеАкты (105665 записей)
CREATE TABLE IF NOT EXISTS Т_НормативныеАкты (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
Статус документа INTEGER,
Тип документа INTEGER,
Подписание документа INTEGER,
Регистрационный номер TEXT,
Регистрационная дата TIMESTAMP,
ИсполнительДокумента INTEGER,
Тема INTEGER,
Заголовок TEXT,
Вид документа INTEGER,
Подлежит ли опубликованию INTEGER,
№ INTEGER,
Опубликовано INTEGER,
Дата публикации TIMESTAMP,
Ответственный исполнитель INTEGER,
Дата контроля TIMESTAMP,
Результат исполнения INTEGER,
С контроля снято INTEGER,
Количество листов INTEGER,
Количество приложений INTEGER,
Дело № TEXT,
Том № TEXT,
Листы INTEGER,
Документ TEXT
);

-- Таблица: Т_НормативныеАкты подчин (105623 записей)
CREATE TABLE IF NOT EXISTS Т_НормативныеАкты подчин (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
КодДокумента INTEGER,
Кто подписал INTEGER
);

-- Таблица: Т_НормативныеАкты подчин2 (266180 записей)
CREATE TABLE IF NOT EXISTS Т_НормативныеАкты подчин2 (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
КодДокумента INTEGER,
Согласовано INTEGER
);

-- Таблица: Т_Опубликовано (2 записей)
CREATE TABLE IF NOT EXISTS Т_Опубликовано (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
Опубликовано TEXT
);

-- Таблица: Т_ОтветственныйИсполнитель (48 записей)
CREATE TABLE IF NOT EXISTS Т_ОтветственныйИсполнитель (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
ФИО TEXT
);

-- Таблица: Т_Подписание (2 записей)
CREATE TABLE IF NOT EXISTS Т_Подписание (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
Подписание TEXT
);

-- Таблица: Т_Согласование (110 записей)
CREATE TABLE IF NOT EXISTS Т_Согласование (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
ФИО TEXT
);

-- Таблица: Т_Статус (7 записей)
CREATE TABLE IF NOT EXISTS Т_Статус (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
Статус документа TEXT
);

-- Таблица: Т_Тема (27 записей)
CREATE TABLE IF NOT EXISTS Т_Тема (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
Тема TEXT
);

-- Таблица: Т_ТипДокумента (3 записей)
CREATE TABLE IF NOT EXISTS Т_ТипДокумента (
Код INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
Тип документа TEXT
);

