import React, { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { supabase } from '../lib/supabase';
import { toast } from 'sonner';

const AuthCallback = () => {
  const navigate = useNavigate();
  const hasProcessed = useRef(false);

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const processCallback = async () => {
      try {
        const { data: { session }, error } = await supabase.auth.getSession();
        if (error) throw error;

        if (session) {
          toast.success('Welcome!');
          navigate('/dashboard', { replace: true });
        } else {
          toast.error('Authentication failed');
          navigate('/login', { replace: true });
        }
      } catch (error) {
        toast.error('Authentication failed. Please try again.');
        navigate('/login', { replace: true });
      }
    };

    processCallback();
  }, [navigate]);

  return (
    <div className="min-h-screen bg-stone-50 flex items-center justify-center">
      <div className="text-center">
        <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-emerald-900 mx-auto mb-4"></div>
        <p className="text-stone-600">Completing sign in...</p>
      </div>
    </div>
  );
};

export default AuthCallback;
