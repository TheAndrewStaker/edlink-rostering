/**
 * Top-level error boundary so a thrown render does not blank the page.
 *
 * Wraps <App /> in main.tsx. Catches errors during render, the
 * commit phase, and in lifecycle methods. Does NOT catch async
 * errors thrown inside event handlers or in TanStack Query queries
 * (those land on the per-query error state).
 *
 * The fallback UI keeps the operator on a usable page: the error
 * message + a stack so support can copy it into a ticket, plus a
 * "Reload" button.
 */

import { Box, Button, Code, Container, Heading, Stack, Text } from "@chakra-ui/react";
import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

export class RootErrorBoundary extends Component<
  { children: ReactNode },
  State
> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface to the dev console so the operator can grab the stack
    // from devtools. Production would also forward to App Insights.
    console.error("RootErrorBoundary caught", error, info);
  }

  render(): ReactNode {
    if (this.state.error === null) {
      return this.props.children;
    }
    return (
      <Box minH="100vh" bg="gray.50" py={16}>
        <Container maxW="container.md">
          <Stack
            gap={4}
            bg="white"
            borderWidth="1px"
            borderColor="red.200"
            borderRadius="lg"
            p={6}
          >
            <Heading size="md" color="red.700">
              The admin app hit an unexpected error.
            </Heading>
            <Text fontSize="sm" color="gray.700">
              The operator queue is unaffected. Reload to recover; if
              the error repeats, copy the message below into a ticket.
            </Text>
            <Code
              display="block"
              whiteSpace="pre-wrap"
              p={3}
              fontSize="xs"
              bg="gray.50"
            >
              {this.state.error.message}
              {"\n\n"}
              {this.state.error.stack ?? ""}
            </Code>
            <Box>
              <Button
                colorPalette="blue"
                onClick={() => window.location.reload()}
              >
                Reload
              </Button>
            </Box>
          </Stack>
        </Container>
      </Box>
    );
  }
}
