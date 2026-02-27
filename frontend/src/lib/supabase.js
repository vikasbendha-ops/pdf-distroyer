import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.REACT_APP_SUPABASE_URL;
const supabaseAnonKey = process.env.REACT_APP_SUPABASE_ANON_KEY;

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

export const SUBSCRIPTION_PLANS = {
  basic: { price: 5.00, name: "Basic", storage_mb: 500, links_per_month: 50 },
  pro: { price: 15.00, name: "Pro", storage_mb: 2000, links_per_month: 200 },
  enterprise: { price: 49.00, name: "Enterprise", storage_mb: 10000, links_per_month: 1000 }
};
