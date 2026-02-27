import { supabase, SUBSCRIPTION_PLANS } from './supabase';

function generateToken() {
  const array = new Uint8Array(32);
  crypto.getRandomValues(array);
  return Array.from(array, b => b.toString(36).padStart(2, '0')).join('').slice(0, 43);
}

export async function getProfile() {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { data, error } = await supabase
    .from('profiles')
    .select('*')
    .eq('id', user.id)
    .maybeSingle();

  if (error) throw error;
  if (!data) throw new Error('Profile not found');

  return {
    user_id: data.id,
    name: data.name,
    email: data.email,
    role: data.role,
    subscription_status: data.subscription_status,
    plan: data.plan,
    storage_used: data.storage_used,
    language: data.language,
    picture: data.picture,
    created_at: data.created_at
  };
}

export async function updateLanguage(language) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');
  const { error } = await supabase.from('profiles').update({ language }).eq('id', user.id);
  if (error) throw error;
}

export async function getFolders() {
  const { data, error } = await supabase
    .from('folders')
    .select('*')
    .order('created_at', { ascending: true });
  if (error) throw error;
  return (data || []).map(f => ({ ...f, folder_id: f.id }));
}

export async function createFolder(name) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');
  const { data, error } = await supabase
    .from('folders')
    .insert({ user_id: user.id, name })
    .select()
    .single();
  if (error) throw error;
  return { ...data, folder_id: data.id };
}

export async function deleteFolder(folderId) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');
  await supabase.from('pdfs').update({ folder_id: null }).eq('folder_id', folderId).eq('user_id', user.id);
  const { error } = await supabase.from('folders').delete().eq('id', folderId).eq('user_id', user.id);
  if (error) throw error;
}

export async function getPdfs() {
  const { data, error } = await supabase
    .from('pdfs')
    .select('*')
    .order('created_at', { ascending: false });
  if (error) throw error;
  return (data || []).map(p => ({ ...p, pdf_id: p.id, folder: p.folder_id }));
}

export async function uploadPdf(file) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const profile = await getProfile();
  if (profile.subscription_status !== 'active') throw new Error('Active subscription required');

  const planInfo = SUBSCRIPTION_PLANS[profile.plan] || SUBSCRIPTION_PLANS.basic;
  const maxStorage = planInfo.storage_mb * 1024 * 1024;
  if (profile.storage_used + file.size > maxStorage) throw new Error('Storage limit exceeded');

  const pdfId = crypto.randomUUID();
  const safeName = `${pdfId}_${Date.now()}.pdf`;
  const storagePath = `${user.id}/${safeName}`;

  const { error: uploadError } = await supabase.storage
    .from('pdfs')
    .upload(storagePath, file, { contentType: 'application/pdf' });
  if (uploadError) throw uploadError;

  const { data, error } = await supabase
    .from('pdfs')
    .insert({ id: pdfId, user_id: user.id, filename: file.name, original_filename: file.name, storage_path: storagePath, file_size: file.size, folder_id: null })
    .select()
    .single();
  if (error) throw error;

  await supabase.from('profiles').update({ storage_used: profile.storage_used + file.size }).eq('id', user.id);
  return { ...data, pdf_id: data.id };
}

export async function renamePdf(pdfId, filename) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');
  const { error } = await supabase.from('pdfs').update({ filename }).eq('id', pdfId).eq('user_id', user.id);
  if (error) throw error;
}

export async function movePdf(pdfId, folderId) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');
  const { error } = await supabase.from('pdfs').update({ folder_id: folderId }).eq('id', pdfId).eq('user_id', user.id);
  if (error) throw error;
}

export async function deletePdf(pdfId) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { data: pdf } = await supabase.from('pdfs').select('*').eq('id', pdfId).eq('user_id', user.id).maybeSingle();
  if (!pdf) throw new Error('PDF not found');

  await supabase.storage.from('pdfs').remove([pdf.storage_path]);
  await supabase.from('pdfs').delete().eq('id', pdfId);
  await supabase.from('links').update({ status: 'revoked' }).eq('pdf_id', pdfId);

  const { data: profileData } = await supabase.from('profiles').select('storage_used').eq('id', user.id).maybeSingle();
  if (profileData) {
    await supabase.from('profiles').update({ storage_used: Math.max(0, (profileData.storage_used || 0) - pdf.file_size) }).eq('id', user.id);
  }
}

export async function getLinks() {
  const { data, error } = await supabase.from('links').select('*').order('created_at', { ascending: false });
  if (error) throw error;
  return (data || []).map(l => ({ ...l, link_id: l.id }));
}

export async function createLink(linkData) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const profile = await getProfile();
  if (profile.subscription_status !== 'active') throw new Error('Active subscription required');

  const token = generateToken();
  let expiryDurationSeconds = null;
  let expiresAt = null;

  if (linkData.expiry_mode === 'countdown') {
    expiryDurationSeconds = (linkData.expiry_hours || 0) * 3600 + (linkData.expiry_minutes || 0) * 60 + (linkData.expiry_seconds || 0);
  } else if (linkData.expiry_mode === 'fixed' && linkData.expiry_fixed_datetime) {
    expiresAt = linkData.expiry_fixed_datetime;
  }

  const { data, error } = await supabase
    .from('links')
    .insert({
      user_id: user.id,
      pdf_id: linkData.pdf_id,
      token,
      expiry_mode: linkData.expiry_mode,
      expiry_duration_seconds: expiryDurationSeconds,
      expiry_fixed_datetime: expiresAt,
      expires_at: expiresAt,
      custom_expired_url: linkData.custom_expired_url || null,
      custom_expired_message: linkData.custom_expired_message || null
    })
    .select()
    .single();
  if (error) throw error;
  return { ...data, link_id: data.id };
}

export async function deleteLink(linkId) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');
  const { error } = await supabase.from('links').delete().eq('id', linkId).eq('user_id', user.id);
  if (error) throw error;
}

export async function revokeLink(linkId) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');
  const { error } = await supabase.from('links').update({ status: 'revoked' }).eq('id', linkId).eq('user_id', user.id);
  if (error) throw error;
}

export async function accessLink(token) {
  const { data: link, error } = await supabase.from('links').select('*').eq('token', token).maybeSingle();
  if (error || !link) throw new Error('Link not found');

  if (link.status === 'revoked') {
    return { status: 'revoked', custom_expired_url: link.custom_expired_url, custom_expired_message: link.custom_expired_message || 'This link has been revoked' };
  }

  const { data: owner } = await supabase.from('profiles').select('subscription_status').eq('id', link.user_id).maybeSingle();
  if (!owner || owner.subscription_status !== 'active') {
    return { status: 'expired', custom_expired_message: "The owner's subscription is inactive" };
  }

  const now = new Date();
  let expiresAt = null;
  let remainingSeconds = null;

  if (link.expiry_mode === 'countdown') {
    const ipSessions = link.ip_sessions || {};
    const clientIp = 'viewer';

    if (ipSessions[clientIp]) {
      const firstOpen = new Date(ipSessions[clientIp].first_open);
      expiresAt = new Date(firstOpen.getTime() + link.expiry_duration_seconds * 1000);
      if (now >= expiresAt) {
        return { status: 'expired', custom_expired_url: link.custom_expired_url, custom_expired_message: link.custom_expired_message || 'Your viewing session has expired' };
      }
      remainingSeconds = Math.max(0, Math.floor((expiresAt - now) / 1000));
    } else {
      ipSessions[clientIp] = { first_open: now.toISOString() };
      expiresAt = new Date(now.getTime() + link.expiry_duration_seconds * 1000);
      remainingSeconds = link.expiry_duration_seconds;
      const updates = { ip_sessions: ipSessions, open_count: (link.open_count || 0) + 1 };
      if (!link.first_open_at) updates.first_open_at = now.toISOString();
      await supabase.from('links').update(updates).eq('id', link.id);
    }
  } else if (link.expiry_mode === 'fixed' && link.expires_at) {
    expiresAt = new Date(link.expires_at);
    if (now >= expiresAt) {
      await supabase.from('links').update({ status: 'expired' }).eq('id', link.id);
      return { status: 'expired', custom_expired_url: link.custom_expired_url, custom_expired_message: link.custom_expired_message || 'This link has expired' };
    }
    remainingSeconds = Math.max(0, Math.floor((expiresAt - now) / 1000));
    await supabase.from('links').update({ open_count: (link.open_count || 0) + 1 }).eq('id', link.id);
  } else {
    await supabase.from('links').update({ open_count: (link.open_count || 0) + 1 }).eq('id', link.id);
  }

  return {
    status: 'active',
    pdf_id: link.pdf_id,
    expires_at: expiresAt ? expiresAt.toISOString() : null,
    remaining_seconds: remainingSeconds,
    expiry_mode: link.expiry_mode,
    watermark_data: { timestamp: now.toISOString(), link_id: link.id }
  };
}

export async function getPdfFileUrl(pdfId) {
  const { data: pdf } = await supabase.from('pdfs').select('storage_path').eq('id', pdfId).maybeSingle();
  if (!pdf) return null;
  const { data } = await supabase.storage.from('pdfs').createSignedUrl(pdf.storage_path, 3600);
  return data?.signedUrl || null;
}

export async function getDashboardStats() {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { data: profile } = await supabase.from('profiles').select('*').eq('id', user.id).maybeSingle();
  const { count: pdfCount } = await supabase.from('pdfs').select('*', { count: 'exact', head: true }).eq('user_id', user.id);
  const { data: userLinks } = await supabase.from('links').select('*').eq('user_id', user.id);

  const links = userLinks || [];
  const totalViews = links.reduce((sum, l) => sum + (l.open_count || 0), 0);
  const plan = profile?.plan || 'none';
  const planInfo = SUBSCRIPTION_PLANS[plan] || { storage_mb: 0 };

  return {
    pdf_count: pdfCount || 0,
    link_count: links.length,
    active_links: links.filter(l => l.status === 'active').length,
    expired_links: links.filter(l => l.status === 'expired').length,
    revoked_links: links.filter(l => l.status === 'revoked').length,
    total_views: totalViews,
    storage_used: profile?.storage_used || 0,
    storage_limit: planInfo.storage_mb * 1024 * 1024,
    plan,
    subscription_status: profile?.subscription_status || 'inactive'
  };
}

export async function getDomains() {
  const { data, error } = await supabase.from('domains').select('*').order('created_at', { ascending: false });
  if (error) throw error;
  return (data || []).map(d => ({ ...d, domain_id: d.id }));
}

export async function addDomain(domain) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');
  const profile = await getProfile();
  if (profile.plan !== 'enterprise') throw new Error('Custom domains require Enterprise plan');
  const verificationToken = generateToken();
  const { data, error } = await supabase.from('domains').insert({ user_id: user.id, domain, verification_token: verificationToken }).select().single();
  if (error) throw error;
  return { ...data, domain_id: data.id };
}

export async function deleteDomain(domainId) {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');
  const { error } = await supabase.from('domains').delete().eq('id', domainId).eq('user_id', user.id);
  if (error) throw error;
}

export async function getAdminStats() {
  const { data: profiles } = await supabase.from('profiles').select('*');
  const { data: allPdfs } = await supabase.from('pdfs').select('id');
  const { data: allLinks } = await supabase.from('links').select('*');

  const users = profiles || [];
  const links = allLinks || [];
  const totalStorage = users.reduce((sum, u) => sum + (u.storage_used || 0), 0);
  const totalViews = links.reduce((sum, l) => sum + (l.open_count || 0), 0);

  return {
    total_users: users.length,
    active_subscribers: users.filter(u => u.subscription_status === 'active').length,
    total_pdfs: (allPdfs || []).length,
    total_links: links.length,
    active_links: links.filter(l => l.status === 'active').length,
    total_storage_bytes: totalStorage,
    total_views: totalViews
  };
}

export async function getAdminUsers() {
  const { data: profiles, error } = await supabase.from('profiles').select('*');
  if (error) throw error;
  const { data: allPdfs } = await supabase.from('pdfs').select('id, user_id');
  const { data: allLinks } = await supabase.from('links').select('id, user_id');
  return (profiles || []).map(u => ({
    ...u,
    user_id: u.id,
    pdf_count: (allPdfs || []).filter(p => p.user_id === u.id).length,
    link_count: (allLinks || []).filter(l => l.user_id === u.id).length
  }));
}

export async function adminUpdateUser(userId, updates) {
  const { error } = await supabase.from('profiles').update(updates).eq('id', userId);
  if (error) throw error;
}

export async function adminDeleteUser(userId) {
  const { error } = await supabase.from('profiles').delete().eq('id', userId);
  if (error) throw error;
}

export async function getAdminLinks() {
  const { data: links, error } = await supabase.from('links').select('*').order('created_at', { ascending: false });
  if (error) throw error;
  const { data: profiles } = await supabase.from('profiles').select('id, name, email');
  const { data: pdfs } = await supabase.from('pdfs').select('id, filename');
  const profileMap = {};
  (profiles || []).forEach(p => { profileMap[p.id] = p; });
  const pdfMap = {};
  (pdfs || []).forEach(p => { pdfMap[p.id] = p; });
  return (links || []).map(l => ({
    ...l,
    link_id: l.id,
    user_name: profileMap[l.user_id]?.name || 'Unknown',
    user_email: profileMap[l.user_id]?.email || 'Unknown',
    pdf_name: pdfMap[l.pdf_id]?.filename || 'Unknown'
  }));
}

export async function adminRevokeLink(linkId) {
  const { error } = await supabase.from('links').update({ status: 'revoked' }).eq('id', linkId);
  if (error) throw error;
}

export async function adminDeleteLink(linkId) {
  const { error } = await supabase.from('links').delete().eq('id', linkId);
  if (error) throw error;
}

export async function getStripeSettings() {
  const { data } = await supabase.from('platform_settings').select('*').eq('key', 'stripe').maybeSingle();
  const value = data?.value || {};
  const mode = value.mode || 'sandbox';
  const storedKey = value.stripe_key || '';
  const hasLiveKey = storedKey.startsWith('sk_live_');
  return {
    mode,
    has_live_key: hasLiveKey,
    active_key_type: hasLiveKey && mode === 'live' ? 'live' : 'sandbox',
    key_preview: storedKey ? `sk_...${storedKey.slice(-4)}` : 'Not configured',
    sandbox_active: mode !== 'live'
  };
}

export async function updateStripeSettings(config) {
  const { data: existing } = await supabase.from('platform_settings').select('*').eq('key', 'stripe').maybeSingle();
  const currentValue = existing?.value || {};
  const newValue = { ...currentValue };
  if (config.stripe_key !== undefined) newValue.stripe_key = config.stripe_key;
  if (config.mode !== undefined) newValue.mode = config.mode;

  if (existing) {
    const { error } = await supabase.from('platform_settings').update({ value: newValue, updated_at: new Date().toISOString() }).eq('key', 'stripe');
    if (error) throw error;
  } else {
    const { error } = await supabase.from('platform_settings').insert({ key: 'stripe', value: newValue, updated_at: new Date().toISOString() });
    if (error) throw error;
  }
}

export function getSubscriptionPlans() {
  return SUBSCRIPTION_PLANS;
}
