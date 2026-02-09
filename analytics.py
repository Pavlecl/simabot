import pandas as pd
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io


class OzonAnalytics:
    def __init__(self):
        self.wh_col = 'Склад отгрузки'
        self.qty_col = 'Количество'
        self.status_col = 'Статус'
        self.date_col = 'Принят в обработку'
        self.price_col = 'Сумма отправления'

    def _read_csv(self, content: bytes):
        for sep in [';', ',']:
            try:
                df = pd.read_csv(io.BytesIO(content), sep=sep, encoding='utf-8')
                if self.qty_col in df.columns:
                    return df
            except:
                continue
        return None

    def process_files(self, fbs_bytes: bytes, fbo_bytes: bytes):
        df_fbs = self._read_csv(fbs_bytes)
        df_fbo = self._read_csv(fbo_bytes)

        if df_fbs is None and df_fbo is None:
            return None, "Ошибка: файлы не распознаны"

        # Обработка FBS (с делением по складам)
        if df_fbs is not None:
            df_fbs = df_fbs[df_fbs[self.status_col] != 'Отменён'].copy()
            df_fbs['Категория Склада'] = df_fbs[self.wh_col]
        else:
            df_fbs = pd.DataFrame()

        # Обработка FBO (все в одну категорию)
        if df_fbo is not None:
            df_fbo = df_fbo[df_fbo[self.status_col] != 'Отменён'].copy()
            df_fbo['Категория Склада'] = 'Склад Ozon (FBO)'
        else:
            df_fbo = pd.DataFrame()

        # Объединя8ем
        df = pd.concat([df_fbs, df_fbo], ignore_index=True)
        if df.empty:
            return None, "Нет данных для анализа (все заказы отменены или файлы пусты)"

        # Работа с датами
        df['Дата'] = pd.to_datetime(df[self.date_col]).dt.date
        df[self.qty_col] = pd.to_numeric(df[self.qty_col], errors='coerce').fillna(0)
        df[self.price_col] = pd.to_numeric(df[self.price_col], errors='coerce').fillna(0)

        # Сводка для текста (Дата -> Склад -> Кол-во)
        brief = df.groupby(['Дата', 'Категория Склада'])[self.qty_col].sum().reset_index()
        brief.columns = ['Дата', 'Склад', 'Количество']
        # Сортируем по дате для красоты
        brief = brief.sort_values(by='Дата', ascending=True)

        # Сводка для Excel (Подробная)
        detailed = df.groupby(['Дата', 'Категория Склада']).agg({
            self.qty_col: 'sum',
            self.price_col: 'sum'
        }).reset_index()
        detailed.columns = ['Дата', 'Склад/Категория', 'Заказано товаров (шт)', 'Сумма (₽)']

        # График по датам
        chart = self._create_chart(df)

        return {"brief": brief, "detailed": detailed, "chart": chart}, None

    def _create_chart(self, df):
        # Группировка для визуализации тренда по дням
        daily = df.groupby(['Дата', 'Категория Склада'])[self.qty_col].sum().unstack().fillna(0)

        plt.figure(figsize=(12, 6))
        for column in daily.columns:
            plt.plot(daily.index, daily[column], marker='o', label=str(column), linewidth=2)

        plt.title('Динамика заказов по датам', fontsize=14)
        plt.xlabel('Дата принятия в обработку')
        plt.ylabel('Количество (шт)')
        plt.grid(True, linestyle='--', alpha=0.6)
        plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        plt.xticks(rotation=45)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=120)
        buf.seek(0)
        plt.close()
        return buf

    def get_excel(self, df):
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        return output