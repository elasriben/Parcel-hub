// Shared domain vocabulary for the UI: French labels + which theme color keys
// represent each parcel state / action / incident type. Colors are resolved at
// render time from useTheme().colors, so we only store the KEY here.

import type { ThemeColors } from "@/src/theme";

type ColorKey = keyof ThemeColors;

export type StateMeta = { label: string; bg: ColorKey; fg: ColorKey };

export const STATE_META: Record<string, StateMeta> = {
  CREATED: { label: "CRÉÉ", bg: "surfaceTertiary", fg: "onSurfaceTertiary" },
  READY_FOR_PICKUP: { label: "PRÊT", bg: "surfaceTertiary", fg: "onSurfaceTertiary" },
  IN_TRANSIT: { label: "EN TRANSIT", bg: "surfaceInverse", fg: "onSurfaceInverse" },
  AT_RELAY_POINT: { label: "AU POINT", bg: "surfaceInverse", fg: "onSurfaceInverse" },
  AVAILABLE_FOR_PICKUP: { label: "DISPONIBLE", bg: "brandPrimary", fg: "onBrandPrimary" },
  PICKUP_AUTHORIZED: { label: "AUTORISÉ", bg: "warning", fg: "onWarning" },
  HANDED_OVER: { label: "REMIS", bg: "success", fg: "onSuccess" },
  DAMAGED: { label: "ENDOMMAGÉ", bg: "error", fg: "onError" },
  INCIDENT: { label: "INCIDENT", bg: "error", fg: "onError" },
  EXPIRED: { label: "EXPIRÉ", bg: "muted", fg: "onSurfaceInverse" },
  CUSTOMER_REFUSED: { label: "REFUSÉ", bg: "error", fg: "onError" },
  RETURN_PENDING: { label: "RETOUR", bg: "surfaceInverse", fg: "onSurfaceInverse" },
  RETURN_READY: { label: "RETOUR PRÊT", bg: "surfaceInverse", fg: "onSurfaceInverse" },
  RETURN_COLLECTED: { label: "REPRIS", bg: "surfaceInverse", fg: "onSurfaceInverse" },
  RETURN_IN_TRANSIT: { label: "RETOUR TRANSIT", bg: "surfaceInverse", fg: "onSurfaceInverse" },
  RETURN_RECEIVED: { label: "RETOUR REÇU", bg: "surfaceInverse", fg: "onSurfaceInverse" },
  RETURNED: { label: "RETOURNÉ", bg: "surfaceInverse", fg: "onSurfaceInverse" },
  CANCELLED: { label: "ANNULÉ", bg: "muted", fg: "onSurfaceInverse" },
};

export function stateMeta(state: string): StateMeta {
  return STATE_META[state] ?? { label: state, bg: "surfaceTertiary", fg: "onSurfaceTertiary" };
}

export const ACTION_LABEL: Record<string, string> = {
  RECEIVE: "RECEVOIR",
  HANDOVER: "REMETTRE LE COLIS",
  COMPLETE: "CONFIRMER : COLIS REMIS",
  REFUSE: "REFUS CLIENT",
  EXPIRE: "MARQUER EXPIRÉ",
  RETURN_CREATE: "CRÉER UN RETOUR",
  RETURN_READY: "PRÊT POUR REPRISE",
  INCIDENT: "SIGNALER UN INCIDENT",
};

export const POINT_STATUS_LABEL: Record<string, string> = {
  ACTIVE: "ACTIF",
  TEMPORARILY_CLOSED: "FERMÉ",
  SUSPENDED: "SUSPENDU",
  FULL: "PLEIN",
  DECOMMISSIONED: "RETIRÉ",
};

export const INCIDENT_TYPES: { value: string; label: string }[] = [
  { value: "DAMAGED_PACKAGE", label: "Colis endommagé" },
  { value: "WRONG_PACKAGE", label: "Mauvais colis" },
  { value: "MISSING_PACKAGE", label: "Colis manquant" },
  { value: "WRONG_POINT", label: "Mauvais point" },
  { value: "CUSTOMER_DISPUTE", label: "Litige client" },
  { value: "OTP_FAILURE", label: "Échec OTP" },
  { value: "DUPLICATE_SCAN", label: "Double scan" },
  { value: "PARTNER_ERROR", label: "Erreur partenaire" },
  { value: "TRANSPORTER_ERROR", label: "Erreur transporteur" },
  { value: "OTHER", label: "Autre" },
];

export const FILTERS: { key: string; label: string }[] = [
  { key: "to_receive", label: "À recevoir" },
  { key: "available", label: "Disponibles" },
  { key: "handed_over", label: "Remis" },
  { key: "returns", label: "Retours" },
  { key: "all", label: "Tous" },
];
