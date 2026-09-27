Message broker carrying order events.

`checkout` produces to it; `accounting` and `fraud-detection` consume. The
browse-and-purchase path does not wait on consumers, so when kafka is
unavailable purchases continue and the consumers stop making progress.
