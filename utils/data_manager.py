import pandas as pd
import os
from models.context import SharedContext

class DataManager:
    """Handles loading, updating, and translating the master product table."""
    def __init__(self, filepath="master_product_table.xlsx"):
        self.filepath = filepath
        self.df = self._load_data()

    def _load_data(self):
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f"File {self.filepath} not found.")
        df = pd.read_excel(self.filepath)
        
        # Ensure derived/missing fields for agents exist
        if 'COGS ($)' not in df.columns:
            df['COGS ($)'] = df['Current Price ($)'] * 0.4  # Assume 40% COGS
            
        if 'Ad Spend ($)' not in df.columns:
            df['Ad Spend ($)'] = df['Revenue ($)'] * 0.1    # Assume 10% Ad Spend
            
        if 'ACoS (%)' not in df.columns:
            # ACoS = Ad Spend / Revenue
            df['ACoS (%)'] = df.apply(
                lambda row: row['Ad Spend ($)'] / row['Revenue ($)'] if row['Revenue ($)'] > 0 else 0.0, 
                axis=1
            )
            
        return df
        
    def save_data(self):
        """Save the in-memory dataframe back to Excel."""
        self.df.to_excel(self.filepath, index=False)
        
    def get_all_products(self):
        return self.df.to_dict('records')
        
    def get_product(self, sku):
        row = self.df[self.df['SKU'] == sku]
        if row.empty:
            return None
        return row.iloc[0].to_dict()
        
    def apply_action(self, sku, action_type, new_value):
        """Applies a specific action to the dataframe."""
        if action_type == "CHANGE_PRICE":
            self.df.loc[self.df['SKU'] == sku, 'Current Price ($)'] = new_value
        elif action_type == "CHANGE_AD_SPEND":
            self.df.loc[self.df['SKU'] == sku, 'Ad Spend ($)'] = new_value
        # Recalculate ACoS
        self.df['ACoS (%)'] = self.df.apply(
            lambda row: row['Ad Spend ($)'] / row['Revenue ($)'] if row['Revenue ($)'] > 0 else 0.0, 
            axis=1
        )
        
    def get_context_for_product(self, sku) -> SharedContext:
        """Constructs a SharedContext suitable for the agents."""
        prod = self.get_product(sku)
        if not prod:
            return None
        
        ctx = SharedContext(
            price=prod['Current Price ($)'],
            cogs=prod['COGS ($)'],
            qty=prod['Inventory On Hand'],
            velocity=prod['Quantity Sold'],
            ad_spend=prod['Ad Spend ($)'],
            acos=prod['ACoS (%)'],
        )
        ctx.compute_derived()
        return ctx
