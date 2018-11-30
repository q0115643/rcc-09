import torch
import config
import train_helper
import data_utils

import numpy as np

from models import vsl_gg
from tensorboardX import SummaryWriter

best_dev_res = test_res = 0


def run(e):
    global best_dev_res, test_res

    e.log.info("*" * 25 + " DATA PREPARATION " + "*" * 25)
    dp = data_utils.data_processor(experiment=e)
    data, W = dp.process()

    label_logvar1_buffer = \
        train_helper.prior_buffer(data.train[1], e.config.zsize,
                                  experiment=e,
                                  freq=e.config.ufl,
                                  name="label_logvar1",
                                  init_path=e.config.prior_file)
    label_mean1_buffer = \
        train_helper.prior_buffer(data.train[1], e.config.zsize,
                                  experiment=e,
                                  freq=e.config.ufl,
                                  name="label_mean1",
                                  init_path=e.config.prior_file)

    label_logvar2_buffer = \
        train_helper.prior_buffer(data.train[1], e.config.ysize,
                                  experiment=e,
                                  freq=e.config.ufl,
                                  name="label_logvar2",
                                  init_path=e.config.prior_file)
    label_mean2_buffer = \
        train_helper.prior_buffer(data.train[1], e.config.ysize,
                                  experiment=e,
                                  freq=e.config.ufl,
                                  name="label_mean2",
                                  init_path=e.config.prior_file)

    all_buffer = [label_logvar1_buffer, label_mean1_buffer,
                  label_logvar2_buffer, label_mean2_buffer]

    e.log.info("labeled buffer size: logvar1: {}, mean1: {}, "
               "logvar2: {}, mean2: {}"
               .format(len(label_logvar1_buffer), len(label_mean1_buffer),
                       len(label_logvar2_buffer), len(label_mean2_buffer)))

    e.log.info("*" * 25 + " DATA PREPARATION " + "*" * 25)
    e.log.info("*" * 25 + " MODEL INITIALIZATION " + "*" * 25)

    model = vsl_gg(
        word_vocab_size=len(data.vocab),
        char_vocab_size=len(data.char_vocab),
        n_tags=len(data.tag_vocab),
        embed_dim=e.config.edim if W is None else W.shape[1],
        embed_init=W,
        experiment=e)

    e.log.info(model)
    e.log.info("*" * 25 + " MODEL INITIALIZATION " + "*" * 25)

    if e.config.summarize:
        writer = SummaryWriter(e.experiment_dir)

    label_batch = data_utils.minibatcher(
        word_data=data.train[1],
        char_data=data.train[2],
        label=data.train[3],
        batch_size=e.config.batch_size,
        shuffle=True)

    evaluator = train_helper.evaluator(data.inv_tag_vocab, model, e)

    e.log.info("Training start ...")
    label_stats = train_helper.tracker(
        ["loss", "logloss", "kl_div", "sup_loss"])

    for it in range(e.config.n_iter):
        model.train()
        kl_temp = train_helper.get_kl_temp(e.config.klr, it, 1.0)

        try:
            l_data, l_mask, l_char, l_char_mask, l_label, l_ixs = \
                next(label_batch)
        except StopIteration:
            pass

        lp_logvar1 = label_logvar1_buffer[l_ixs]
        lp_mean1 = label_mean1_buffer[l_ixs]
        lp_logvar2 = label_logvar2_buffer[l_ixs]
        lp_mean2 = label_mean2_buffer[l_ixs]

        l_loss, l_logloss, l_kld, sup_loss, \
            lq_mean1, lq_logvar1, lq_mean2, lq_logvar2, _ = \
            model(l_data, l_mask, l_char, l_char_mask,
                  l_label, [lp_mean1, lp_mean2], [lp_logvar1, lp_logvar2],
                  kl_temp)

        label_logvar1_buffer.update_buffer(l_ixs, lq_logvar1, l_mask.sum(-1))
        label_mean1_buffer.update_buffer(l_ixs, lq_mean1, l_mask.sum(-1))

        label_logvar2_buffer.update_buffer(l_ixs, lq_logvar2, l_mask.sum(-1))
        label_mean2_buffer.update_buffer(l_ixs, lq_mean2, l_mask.sum(-1))

        label_stats.update(
            {"loss": l_loss, "logloss": l_logloss, "kl_div": l_kld,
             "sup_loss": sup_loss}, l_mask.sum())

        model.optimize(l_loss)

        if (it + 1) % e.config.print_every == 0:
            summary = label_stats.summarize(
                "it: {} (max: {}), kl_temp: {:.2f}, labeled".format(
                    it + 1, len(label_batch), kl_temp))
            e.log.info(summary)
            if e.config.summarize:
                writer.add_scalar(
                    "label/kl_temp", kl_temp, it)
                for name, value in label_stats.stats.items():
                    writer.add_scalar(
                        "label/" + name, value, it)
            label_stats.reset()
        if (it + 1) % e.config.eval_every == 0:

            e.log.info("*" * 25 + " DEV SET EVALUATION " + "*" * 25)

            dev_perf, dev_res = evaluator.evaluate(data.dev)

            e.log.info("*" * 25 + " DEV SET EVALUATION " + "*" * 25)

            if e.config.summarize:
                for n, v in dev_perf.items():
                    writer.add_scalar(
                        "dev/" + n, v, it)

            if best_dev_res < dev_res:
                best_dev_res = dev_res

                e.log.info("*" * 25 + " TEST SET EVALUATION " + "*" * 25)

                test_perf, test_res = evaluator.evaluate(data.test)

                e.log.info("*" * 25 + " TEST SET EVALUATION " + "*" * 25)

                model.save(
                    dev_perf=dev_perf,
                    test_perf=test_perf,
                    iteration=it)

                if e.config.save_prior:
                    for buf in all_buffer:
                        buf.save()

                if e.config.summarize:
                    writer.add_scalar(
                        "dev/best_result", best_dev_res, it)
                    for n, v in test_perf.items():
                        writer.add_scalar(
                            "test/" + n, v, it)
            e.log.info("best dev result: {:.4f}, "
                       "test result: {:.4f}, "
                       .format(best_dev_res, test_res))
            label_stats.reset()


if __name__ == '__main__':

    args = config.get_parser().parse_args()
    args.use_cuda = torch.cuda.is_available()

    def exit_handler(*args):
        print(args)
        print("best dev result: {:.4f}, "
              "test result: {:.4f}"
              .format(best_dev_res, test_res))
        exit()

    train_helper.register_exit_handler(exit_handler)

    np.random.seed(args.random_seed)
    torch.manual_seed(args.random_seed)
    with train_helper.experiment(args, args.prefix) as e:

        e.log.info("*" * 25 + " ARGS " + "*" * 25)
        e.log.info(args)
        e.log.info("*" * 25 + " ARGS " + "*" * 25)

        run(e)
