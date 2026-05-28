/**
 * Shared component-test render helper.
 *
 * Wraps a component under test in the providers the real app mounts:
 * ChakraProvider for the Chakra v3 design system and
 * QueryClientProvider for any component that calls into TanStack
 * Query. Each test gets a fresh QueryClient with retries off so a
 * failed mutation surfaces as an error instead of looping.
 */

import { ChakraProvider, defaultSystem } from "@chakra-ui/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

interface WrapperProps {
  children: ReactNode;
}

export function renderWithProviders(
  ui: ReactElement,
  options: { queryClient?: QueryClient } & Omit<RenderOptions, "wrapper"> = {},
) {
  const queryClient = options.queryClient ?? makeQueryClient();
  function Wrapper({ children }: WrapperProps) {
    return (
      <ChakraProvider value={defaultSystem}>
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      </ChakraProvider>
    );
  }
  return {
    queryClient,
    ...render(ui, { wrapper: Wrapper, ...options }),
  };
}
