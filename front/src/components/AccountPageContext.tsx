import { ArrowLeft, ChevronRight, List, Users } from "lucide-react";
import { Link } from "react-router-dom";

export type AccountPageCrumb = {
  label: string;
  to?: string;
};

export function AccountPageContext({
  crumbs = [],
  backTo,
  backLabel,
}: {
  crumbs?: AccountPageCrumb[];
  backTo?: string;
  backLabel?: string;
}) {
  return (
    <div className="flex flex-col gap-2 border-b border-slate-200 pb-3 sm:flex-row sm:items-center sm:justify-between">
      <nav className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-slate-500" aria-label="账号中心当前位置">
        <Link to="/accounts" className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-2 hover:bg-white hover:text-slate-950">
          <Users className="h-3.5 w-3.5" aria-hidden="true" />
          账号中心
        </Link>
        {crumbs.map((crumb, index) => (
          <span key={`${crumb.label}-${index}`} className="inline-flex min-w-0 items-center gap-1.5">
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-300" aria-hidden="true" />
            {crumb.to ? (
              <Link to={crumb.to} className="min-h-8 truncate rounded-md px-2 py-2 hover:bg-white hover:text-slate-950">
                {crumb.label}
              </Link>
            ) : (
              <span className="truncate px-2 font-medium text-slate-800" aria-current="page">{crumb.label}</span>
            )}
          </span>
        ))}
      </nav>
      <div className="flex flex-wrap gap-2 self-start sm:self-auto">
        {backTo && backLabel ? (
          <Link to={backTo} className="inline-flex min-h-9 shrink-0 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 hover:bg-slate-50">
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            {backLabel}
          </Link>
        ) : null}
        <Link to="/accounts" className="inline-flex min-h-9 shrink-0 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 hover:bg-slate-50">
          <List className="h-3.5 w-3.5" aria-hidden="true" />
          返回账号列表
        </Link>
      </div>
    </div>
  );
}
