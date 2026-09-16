// RELAY design tokens — "5 Brutalist Mobile" (light only).
// Values mirror /app/design_guidelines.json. Zero radius, 2pt black borders,
// solid high-contrast surfaces, Industrial Orange as the single action color.

import { useMemo } from "react";
import { Appearance, StyleSheet, useColorScheme } from "react-native";

export type ColorScheme = "light" | "dark";

const light = {
  surface: "#FFFFFF",
  onSurface: "#000000",
  surfaceSecondary: "#F4F4F4",
  onSurfaceSecondary: "#000000",
  surfaceTertiary: "#E5E5E5",
  onSurfaceTertiary: "#000000",
  surfaceInverse: "#000000",
  onSurfaceInverse: "#FFFFFF",
  muted: "#666666",

  brand: "#FF3B00",
  onBrand: "#000000",
  brandPrimary: "#FF3B00",
  onBrandPrimary: "#000000",
  brandSecondary: "#000000",
  onBrandSecondary: "#FFFFFF",
  brandTertiary: "#FFE4DB",
  onBrandTertiary: "#FF3B00",

  success: "#008A27",
  onSuccess: "#FFFFFF",
  warning: "#FFC000",
  onWarning: "#000000",
  error: "#D90000",
  onError: "#FFFFFF",
  info: "#0000FF",
  onInfo: "#FFFFFF",

  border: "#000000",
  borderStrong: "#000000",
  divider: "#000000",
};

export type ThemeColors = typeof light;

export const defaultScheme = "light" satisfies ColorScheme;
export const themes: { light: ThemeColors; dark?: ThemeColors } = { light };

// Typography, spacing, radius — used directly by components (non-color tokens).
export const fonts = {
  display: "SpaceGrotesk_500Medium",
  mono: "IBMPlexMono_500Medium",
  monoRegular: "IBMPlexMono_400Regular",
};

export const fontSize = { sm: 12, base: 14, lg: 16, xl: 20, "2xl": 24, "3xl": 32, "4xl": 44 };

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, "2xl": 32, "3xl": 48 };

export const radii = { sm: 0, md: 0, lg: 0, pill: 0 };

export const BORDER = 2; // hard 2pt brutalist border

export function setColorScheme(scheme: ColorScheme | null) {
  Appearance.setColorScheme?.(scheme);
}
setColorScheme?.(themes.dark ? null : defaultScheme);

export function useTheme(): { scheme: ColorScheme; colors: ThemeColors } {
  const system = useColorScheme();
  const scheme: ColorScheme = system && themes[system] ? system : defaultScheme;
  return { scheme, colors: themes[scheme] ?? themes.light };
}

export function makeStyles<T extends StyleSheet.NamedStyles<T> | StyleSheet.NamedStyles<any>>(
  factory: (colors: ThemeColors) => T & StyleSheet.NamedStyles<any>,
): () => T {
  return function useStyles(): T {
    const { colors } = useTheme();
    return useMemo(() => StyleSheet.create(factory(colors)), [colors]);
  };
}
