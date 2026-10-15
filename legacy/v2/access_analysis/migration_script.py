import sqlite3
import pandas as pd
from pathlib import Path
import json

class AccessToSQLiteMigrator:
    def __init__(self, sqlite_db_path="documents.db"):
        self.sqlite_db = sqlite_db_path
        self.data_dir = Path("access_data")
    
    def migrate_all_data(self):
        """Миграция всех данных из CSV в SQLite"""
        conn = sqlite3.connect(self.sqlite_db)
        
        try:

            # Миграция таблицы Т_ВидДокумента
            print(f"🔄 Мигрируем Т_ВидДокумента...")
            df_Т_ВидДокумента = pd.read_csv(self.data_dir / "Т_ВидДокумента.csv")
            df_Т_ВидДокумента.to_sql("Т_ВидДокумента", conn, if_exists="replace", index=False)
            print(f"✅ Т_ВидДокумента: {len(df_Т_ВидДокумента)} записей")

            # Миграция таблицы Т_Исполнитель
            print(f"🔄 Мигрируем Т_Исполнитель...")
            df_Т_Исполнитель = pd.read_csv(self.data_dir / "Т_Исполнитель.csv")
            df_Т_Исполнитель.to_sql("Т_Исполнитель", conn, if_exists="replace", index=False)
            print(f"✅ Т_Исполнитель: {len(df_Т_Исполнитель)} записей")

            # Миграция таблицы Т_КтоПодписал
            print(f"🔄 Мигрируем Т_КтоПодписал...")
            df_Т_КтоПодписал = pd.read_csv(self.data_dir / "Т_КтоПодписал.csv")
            df_Т_КтоПодписал.to_sql("Т_КтоПодписал", conn, if_exists="replace", index=False)
            print(f"✅ Т_КтоПодписал: {len(df_Т_КтоПодписал)} записей")

            # Миграция таблицы Т_НормативныеАкты
            print(f"🔄 Мигрируем Т_НормативныеАкты...")
            df_Т_НормативныеАкты = pd.read_csv(self.data_dir / "Т_НормативныеАкты.csv")
            df_Т_НормативныеАкты.to_sql("Т_НормативныеАкты", conn, if_exists="replace", index=False)
            print(f"✅ Т_НормативныеАкты: {len(df_Т_НормативныеАкты)} записей")

            # Миграция таблицы Т_НормативныеАкты подчин
            print(f"🔄 Мигрируем Т_НормативныеАкты подчин...")
            df_Т_НормативныеАкты подчин = pd.read_csv(self.data_dir / "Т_НормативныеАкты подчин.csv")
            df_Т_НормативныеАкты подчин.to_sql("Т_НормативныеАкты подчин", conn, if_exists="replace", index=False)
            print(f"✅ Т_НормативныеАкты подчин: {len(df_Т_НормативныеАкты подчин)} записей")

            # Миграция таблицы Т_НормативныеАкты подчин2
            print(f"🔄 Мигрируем Т_НормативныеАкты подчин2...")
            df_Т_НормативныеАкты подчин2 = pd.read_csv(self.data_dir / "Т_НормативныеАкты подчин2.csv")
            df_Т_НормативныеАкты подчин2.to_sql("Т_НормативныеАкты подчин2", conn, if_exists="replace", index=False)
            print(f"✅ Т_НормативныеАкты подчин2: {len(df_Т_НормативныеАкты подчин2)} записей")

            # Миграция таблицы Т_Опубликовано
            print(f"🔄 Мигрируем Т_Опубликовано...")
            df_Т_Опубликовано = pd.read_csv(self.data_dir / "Т_Опубликовано.csv")
            df_Т_Опубликовано.to_sql("Т_Опубликовано", conn, if_exists="replace", index=False)
            print(f"✅ Т_Опубликовано: {len(df_Т_Опубликовано)} записей")

            # Миграция таблицы Т_ОтветственныйИсполнитель
            print(f"🔄 Мигрируем Т_ОтветственныйИсполнитель...")
            df_Т_ОтветственныйИсполнитель = pd.read_csv(self.data_dir / "Т_ОтветственныйИсполнитель.csv")
            df_Т_ОтветственныйИсполнитель.to_sql("Т_ОтветственныйИсполнитель", conn, if_exists="replace", index=False)
            print(f"✅ Т_ОтветственныйИсполнитель: {len(df_Т_ОтветственныйИсполнитель)} записей")

            # Миграция таблицы Т_Подписание
            print(f"🔄 Мигрируем Т_Подписание...")
            df_Т_Подписание = pd.read_csv(self.data_dir / "Т_Подписание.csv")
            df_Т_Подписание.to_sql("Т_Подписание", conn, if_exists="replace", index=False)
            print(f"✅ Т_Подписание: {len(df_Т_Подписание)} записей")

            # Миграция таблицы Т_Согласование
            print(f"🔄 Мигрируем Т_Согласование...")
            df_Т_Согласование = pd.read_csv(self.data_dir / "Т_Согласование.csv")
            df_Т_Согласование.to_sql("Т_Согласование", conn, if_exists="replace", index=False)
            print(f"✅ Т_Согласование: {len(df_Т_Согласование)} записей")

            # Миграция таблицы Т_Статус
            print(f"🔄 Мигрируем Т_Статус...")
            df_Т_Статус = pd.read_csv(self.data_dir / "Т_Статус.csv")
            df_Т_Статус.to_sql("Т_Статус", conn, if_exists="replace", index=False)
            print(f"✅ Т_Статус: {len(df_Т_Статус)} записей")

            # Миграция таблицы Т_Тема
            print(f"🔄 Мигрируем Т_Тема...")
            df_Т_Тема = pd.read_csv(self.data_dir / "Т_Тема.csv")
            df_Т_Тема.to_sql("Т_Тема", conn, if_exists="replace", index=False)
            print(f"✅ Т_Тема: {len(df_Т_Тема)} записей")

            # Миграция таблицы Т_ТипДокумента
            print(f"🔄 Мигрируем Т_ТипДокумента...")
            df_Т_ТипДокумента = pd.read_csv(self.data_dir / "Т_ТипДокумента.csv")
            df_Т_ТипДокумента.to_sql("Т_ТипДокумента", conn, if_exists="replace", index=False)
            print(f"✅ Т_ТипДокумента: {len(df_Т_ТипДокумента)} записей")

            conn.commit()
            print("🎉 Миграция завершена!")
            
        except Exception as e:
            print(f"❌ Ошибка миграции: {e}")
            conn.rollback()
        finally:
            conn.close()

if __name__ == "__main__":
    migrator = AccessToSQLiteMigrator()
    migrator.migrate_all_data()
