import React, { useState } from 'react';
import { Sparkles } from 'lucide-react';

export default function App() {
  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-slate-50 text-slate-900 p-6">
      <div className="p-4 bg-white rounded-2xl shadow-sm border border-slate-200 flex items-center space-x-3">
        <Sparkles className="w-6 h-6 text-indigo-600" />
        <span className="font-semibold text-lg">Feature App Initialized</span>
      </div>
    </div>
  );
}
