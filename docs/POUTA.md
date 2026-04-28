# GPU Server Deployment on CSC Pouta

Quick guide to launching a GPU-enabled virtual machine in CSC's cPouta cloud. For full details, see the [official documentation](https://docs.csc.fi/cloud/pouta/).

---

## Requirements

- CSC user account ([create one](https://docs.csc.fi/accounts/how-to-create-new-user-account/))
- Multi-Factor Authentication (MFA) ([setup guide](https://docs.csc.fi/accounts/mfa/))
- CSC project with cPouta access ([project setup](https://docs.csc.fi/accounts/how-to-create-new-project/), [add service](https://docs.csc.fi/accounts/how-to-add-service-access-for-project/))

## 1. Log in to cPouta

1. Go to [https://pouta.csc.fi](https://pouta.csc.fi/)
2. Log in with your CSC account and MFA
3. Select your project ([my.csc.fi](https://my.csc.fi/))

## 2. Set up SSH keys

1. Go to **Compute > Key Pairs**
2. Import your public SSH key or create a new key pair
3. To save the private key securely on Linux or Mac:
   ```bash
   mkdir -p ~/.ssh
   chmod 700 ~/.ssh
   mv keyname.pem ~/.ssh
   chmod 400 ~/.ssh/keyname.pem
   ```

## 3. Configure security groups

- Go to **Network > Security Groups**
- Create a group (e.g., `SSH-Access`)
- Add a rule to allow SSH (port 22) from your IP/subnet
- Assign this group to your VM at launch

## 4. Launch a GPU virtual machine

1. Go to **Compute > Instances > Launch Instance**
2. Fill in:
   - **Instance Name**
   - **Source**: Select an image (recommended: `Ubuntu-24.04`)
   - **Flavor**: Choose a GPU flavor ([see below](/docs/POUTA.md#gpu-flavors-cpouta))
   - **Security Groups**: Assign your SSH group
   - **Key Pair**: Select your SSH key
3. Click **Launch Instance**

### GPU Flavors for cPouta

|    Flavor    | Cores | GPUs | Memory (GiB) |
| :----------: | :---: | :--: | :----------: |
| `gpu.1.1gpu` |  14   |  1   |     117      |
| `gpu.1.2gpu` |  28   |  2   |     234      |
| `gpu.1.4gpu` |  56   |  4   |     468      |

[Full flavor list](https://docs.csc.fi/cloud/pouta/vm-flavors-and-billing/#gpu-flavors)

## 5. Assign a floating IP

- After the VM is running, go to **Compute > Instances**
- Under **Actions**, select **Associate Floating IP**
- Allocate a new IP if needed, and associate it with your VM

## 6. Connect via SSH

- Use your private key:

  ```bash
  ssh -i ~/.ssh/keyname.pem <user>@<floating-ip>
  ```

- Default username depends on the image (e.g., `ubuntu`)

## 7. Deploy the application

See the [setup](/docs/SETUP.md) documentation.

## References

- [CSC Pouta Documentation](https://docs.csc.fi/cloud/pouta/)
- [Launching a VM](https://docs.csc.fi/cloud/pouta/launch-vm-from-web-gui/)
- [GPU Flavors](https://docs.csc.fi/cloud/pouta/vm-flavors-and-billing/#gpu-flavors)
- [Connecting to VM](https://docs.csc.fi/cloud/pouta/connecting-to-vm/)
